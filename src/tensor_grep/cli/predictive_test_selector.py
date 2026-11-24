from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any

from tensor_grep.cli.lang_suffixes import JS_TS_SUFFIXES as _JS_TS_SUFFIXES

_GENERIC_STEMS = frozenset({"__init__", "init", "main", "cli", "app", "index", "utils", "helpers"})
_STOP_TOKENS = frozenset({
    "test",
    "tests",
    "spec",
    "unit",
    "e2e",
    "integration",
    "run",
    "get",
    "set",
})


def _bounded_git_cochanges(
    validation_root: Path,
    source_path: Path,
    *,
    deadline_monotonic: float | None = None,
) -> set[str]:
    if deadline_monotonic is not None and (deadline_monotonic - time.monotonic()) < 0.75:
        return set()
    if not (validation_root / ".git").exists():
        return set()
    try:
        rel_source = os.path.relpath(source_path, validation_root)
        cmd = [
            "git",
            "-C",
            str(validation_root),
            "log",
            "-z",
            "--full-diff",
            "-n",
            "30",
            "--name-only",
            "--pretty=format:",
            "--",
            rel_source,
        ]
        proc = subprocess.run(cmd, capture_output=True, timeout=0.5, check=False)
    except (OSError, ValueError, subprocess.SubprocessError):
        return set()
    if proc.returncode != 0 or not proc.stdout:
        return set()
    cochanged: set[str] = set()
    for raw_entry in proc.stdout.split(b"\x00"):
        cleaned = os.fsdecode(raw_entry).strip()
        if cleaned:
            cochanged.add(os.path.normpath(os.path.join(validation_root, cleaned)))
    return cochanged


def _read_test_text(test_path: str, repo_map_mod: Any) -> str:
    if repo_map_mod is not None and hasattr(repo_map_mod, "_read_source_text_cached"):
        try:
            return str(repo_map_mod._read_source_text_cached(test_path))
        except (OSError, UnicodeDecodeError):
            return ""
    try:
        return Path(test_path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _meaningful_tokens(value: str | None) -> set[str]:
    if not value:
        return set()
    parts = re.split(r"[^a-zA-Z0-9]+", value.lower())
    return {p for p in parts if len(p) >= 3 and p not in _STOP_TOKENS}


def _related_stem(stem: str, repo_map_mod: Any) -> str:
    if repo_map_mod is not None and hasattr(repo_map_mod, "_related_test_stem"):
        return str(repo_map_mod._related_test_stem(stem))
    return stem.removeprefix("test_")


def _scan_non_python_code_and_imports(text: str) -> tuple[str, list[str]]:
    """Scan JS/TS-like code stripping comments, strings, and regexes while keeping template ${...} code."""
    n = len(text)
    i = 0
    code_parts: list[str] = []
    import_specs: list[str] = []
    prev_word_or_op = ""
    can_start_regex = True

    def _skip_quoted(idx: int, quote: str) -> tuple[int, str]:
        idx += 1
        chars: list[str] = []
        while idx < n:
            ch = text[idx]
            if ch == "\\" and idx + 1 < n:
                chars.append(text[idx : idx + 2])
                idx += 2
                continue
            if ch == quote:
                return idx + 1, "".join(chars)
            chars.append(ch)
            idx += 1
        return n, "".join(chars)

    def _scan_template(idx: int) -> int:
        idx += 1
        while idx < n:
            ch = text[idx]
            if ch == "\\" and idx + 1 < n:
                idx += 2
                continue
            if ch == "`":
                return idx + 1
            if ch == "$" and idx + 1 < n and text[idx + 1] == "{":
                idx += 2
                depth = 1
                expr_start = idx
                while idx < n and depth > 0:
                    c = text[idx]
                    if c in ('"', "'"):
                        idx, _ = _skip_quoted(idx, c)
                        continue
                    if c == "`":
                        idx = _scan_template(idx)
                        continue
                    if c == "{":
                        depth += 1
                    elif c == "}":
                        depth -= 1
                        if depth == 0:
                            sub_code, sub_specs = _scan_non_python_code_and_imports(
                                text[expr_start:idx]
                            )
                            code_parts.append(" " + sub_code + " ")
                            import_specs.extend(sub_specs)
                            idx += 1
                            break
                    idx += 1
                continue
            idx += 1
        return n

    while i < n:
        ch = text[i]
        if ch == "/" and i + 1 < n and text[i + 1] == "/":
            nl = text.find("\n", i + 2)
            i = n if nl == -1 else nl
            code_parts.append(" ")
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            end = text.find("*/", i + 2)
            i = n if end == -1 else end + 2
            code_parts.append(" ")
            continue
        if ch == "/" and can_start_regex:
            j = i + 1
            in_class = False
            while j < n:
                rc = text[j]
                if rc == "\\" and j + 1 < n:
                    j += 2
                    continue
                if rc == "[":
                    in_class = True
                elif rc == "]" and in_class:
                    in_class = False
                elif rc == "/" and not in_class:
                    j += 1
                    while j < n and text[j].isalpha():
                        j += 1
                    break
                elif rc == "\n":
                    break
                j += 1
            i = j
            code_parts.append(" ")
            can_start_regex = False
            continue
        if ch in ('"', "'"):
            i, inner = _skip_quoted(i, ch)
            if prev_word_or_op in {"from", "import", "require("}:
                import_specs.append(inner)
            code_parts.append(" ")
            can_start_regex = False
            prev_word_or_op = ""
            continue
        if ch == "`":
            i = _scan_template(i)
            code_parts.append(" ")
            can_start_regex = False
            prev_word_or_op = ""
            continue
        code_parts.append(ch)
        if not ch.isspace():
            can_start_regex = ch in "=([:,!&|?;{}~+-*%^<>/"
            if ch.isalnum() or ch in ("_", "$"):
                start = i
                while i < n and (text[i].isalnum() or text[i] in ("_", "$")):
                    i += 1
                word = text[start:i]
                code_parts.pop()
                code_parts.append(word)
                prev_word_or_op = word
                can_start_regex = word in {"return", "typeof", "throw", "case", "else", "yield"}
                continue
            prev_word_or_op = "require(" if (ch == "(" and prev_word_or_op == "require") else ch
        i += 1
    return "".join(code_parts), import_specs


def _python_ast_imports_and_names(text: str) -> tuple[set[str], set[str]] | None:
    """Return (imported_module_tokens, referenced_identifiers) for Python code, or None on SyntaxError."""
    import ast

    try:
        tree = ast.parse(text)
    except SyntaxError:
        return None
    imported: set[str] = set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.update(part.lower() for part in alias.name.split("."))
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported.update(part.lower() for part in node.module.split("."))
            for alias in node.names:
                imported.add(alias.name.lower())
        elif isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    return imported, names


def _classify_test_evidence(
    *,
    test_path: str,
    primary_file: str | None,
    primary_symbol_name: str | None,
    query: str | None,
    repo_map_mod: Any,
    git_cochanges: set[str] | None = None,
    path_score: int = 0,
) -> tuple[int, float, int]:
    """Classify a candidate test into Tiers 1-4 and return (tier, confidence, tie_score)."""
    del query
    test_p = Path(test_path)
    test_related_stem = _related_stem(test_p.stem.lower(), repo_map_mod)
    source_stem = Path(primary_file).stem.lower() if primary_file else ""
    source_related_stem = _related_stem(source_stem, repo_map_mod) if source_stem else ""
    is_mirror = bool(source_related_stem and test_related_stem == source_related_stem)
    text = _read_test_text(test_path, repo_map_mod)

    # Tier 1 (0.85-0.95): Direct AST import or code reference (never comments/strings alone).
    direct_import = False
    symbol_ref = False
    stem_ref = False
    ast_info = (
        _python_ast_imports_and_names(text) if (text and test_p.suffix.lower() == ".py") else None
    )
    if ast_info is not None:
        imported_tokens, code_names = ast_info
        if source_stem and source_stem not in _GENERIC_STEMS:
            direct_import = source_stem in imported_tokens
            stem_ref = len(source_stem) >= 4 and (
                source_stem in imported_tokens or any(n.lower() == source_stem for n in code_names)
            )
        if primary_symbol_name and len(primary_symbol_name) >= 3:
            symbol_ref = (
                primary_symbol_name in code_names or primary_symbol_name.lower() in imported_tokens
            )
    elif text:
        code_only, import_specs = _scan_non_python_code_and_imports(text)
        if source_stem and source_stem not in _GENERIC_STEMS:
            escaped = re.escape(source_stem)
            direct_import = any(re.search(rf"\b{escaped}\b", spec) for spec in import_specs)
            stem_ref = len(source_stem) >= 4 and bool(re.search(rf"\b{escaped}\b", code_only))
        if primary_symbol_name and len(primary_symbol_name) >= 3:
            symbol_ref = bool(re.search(rf"\b{re.escape(primary_symbol_name)}\b", code_only))
    if direct_import or symbol_ref or stem_ref:
        both_or_mirror = (direct_import and (symbol_ref or is_mirror)) or (symbol_ref and is_mirror)
        conf = 0.95 if both_or_mirror else (0.92 if (direct_import or symbol_ref) else 0.88)
        tie = (
            (4 if direct_import else 0)
            + (4 if symbol_ref else 0)
            + (4 if is_mirror else 0)
            + (2 if stem_ref else 0)
        )
        return (1, conf, tie + path_score)

    # Tier 2 (0.75-0.85): Basename / mirror-path match.
    if is_mirror:
        in_tests = any(part in {"tests", "test", "unit"} for part in test_p.parts[:-1])
        return (2, 0.84 if in_tests else 0.80, 8 + path_score)

    # Tier 3 (0.60-0.80): Partial stem/symbol overlap or bounded local git co-change.
    partial_stem = bool(
        source_related_stem
        and test_related_stem
        and (
            source_related_stem in test_related_stem
            or (len(test_related_stem) >= 5 and test_related_stem in source_related_stem)
        )
    )
    in_cochange = bool(git_cochanges and test_path in git_cochanges)
    src_toks = _meaningful_tokens(source_related_stem) | _meaningful_tokens(primary_symbol_name)
    overlap = len(src_toks & _meaningful_tokens(test_related_stem))
    if partial_stem or in_cochange or overlap >= 2:
        conf = (
            0.76
            if (partial_stem and in_cochange)
            else (0.70 if (partial_stem or in_cochange) else 0.64)
        )
        tie = (4 if partial_stem else 0) + (3 if in_cochange else 0) + overlap
        return (3, conf, tie + path_score)

    # Tier 4 (<= 0.55, NEVER 0.95): Generic fallback / weak keyword match.
    conf = round(min(0.55, 0.42 + 0.03 * min(max(path_score, 0), 4)), 3)
    return (4, conf, path_score)


def calibrate_test_step_confidence(
    *,
    test_path: str,
    scope: str,
    primary_file: str | None = None,
    primary_symbol_name: str | None = None,
    query: str | None = None,
    repo_map_mod: Any = None,
) -> float:
    """Calibrate validation step confidence from actual test-to-target evidence tier."""
    git_cochanges: set[str] | None = None
    if primary_file:
        src = Path(primary_file)
        if repo_map_mod is not None and hasattr(repo_map_mod, "_validation_repo_root"):
            val_root = repo_map_mod._validation_repo_root(src.parent)
        else:
            val_root = src.parent
        git_cochanges = _bounded_git_cochanges(val_root, src)
    tier, conf, _ = _classify_test_evidence(
        test_path=test_path,
        primary_file=primary_file,
        primary_symbol_name=primary_symbol_name,
        query=query,
        repo_map_mod=repo_map_mod,
        git_cochanges=git_cochanges,
    )
    if scope == "symbol":
        return conf
    if tier == 1:
        return round(min(0.85, max(0.78, conf - 0.08)), 3)
    if tier == 2:
        return round(min(0.82, max(0.75, conf - 0.02)), 3)
    if tier == 3:
        return round(min(0.72, max(0.60, conf - 0.04)), 3)
    return round(min(0.52, conf), 3)


def _collect_candidate_files(
    *,
    repo_map_mod: Any,
    validation_root: Path,
    source_path: Path,
    precomputed_file_paths: list[str | Path] | None,
    deadline_monotonic: float | None,
    deadline_hit: Any,
    unreadable_hit: Any,
) -> list[Path]:
    scan_limit = getattr(repo_map_mod, "_VALIDATION_RUNNER_SCAN_LIMIT", 512)
    raw_candidates = repo_map_mod._precomputed_validation_files_for_root(
        validation_root,
        precomputed_file_paths,
        deadline_monotonic=deadline_monotonic,
        deadline_hit=deadline_hit,
    )
    if raw_candidates is None:
        walk_kw = {
            "max_files": scan_limit,
            "deadline_monotonic": deadline_monotonic,
            "deadline_hit": deadline_hit,
            "unreadable_hit": unreadable_hit,
        }
        candidates = list(repo_map_mod._iter_repo_files(validation_root, **walk_kw))
        for tests_sub in (
            validation_root / "tests" / "unit",
            validation_root / "tests",
            validation_root / "test",
        ):
            if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
                break
            if tests_sub.is_dir():
                candidates.extend(repo_map_mod._iter_repo_files(tests_sub, **walk_kw))
    else:
        candidates = list(raw_candidates)

    stem = source_path.stem
    for probe in (
        validation_root / "tests" / "unit" / f"test_{stem}.py",
        validation_root / "tests" / f"test_{stem}.py",
        source_path.parent / f"test_{stem}.py",
    ):
        if probe.is_file():
            candidates.append(probe)
    return candidates


def discover_and_rank_tests_for_primary_file(
    scoped_root: str | Path,
    primary_file: str | None,
    *,
    primary_symbol_name: str | None,
    query: str,
    limit: int,
    precomputed_file_paths: list[str | Path] | None = None,
    deadline_monotonic: float | None = None,
    deadline_hit: Any = None,
    unreadable_hit: Any = None,
    repo_map_mod: Any = None,
) -> list[str]:
    if not primary_file or repo_map_mod is None:
        return []
    source_path = repo_map_mod._resolved_input_path(primary_file)
    validation_root = repo_map_mod._validation_repo_root(source_path.parent)
    scoped_path = repo_map_mod._resolved_input_path(scoped_root)
    if repo_map_mod._is_dir_with_receipt(scoped_path) and validation_root == scoped_path:
        return []

    terms: list[str] = []
    for term in [
        *repo_map_mod._candidate_terms(primary_symbol_name),
        *repo_map_mod._candidate_terms(query),
        *repo_map_mod._source_tokens([str(source_path)]),
    ]:
        if term and term not in terms:
            terms.append(term)

    candidate_files = _collect_candidate_files(
        repo_map_mod=repo_map_mod,
        validation_root=validation_root,
        source_path=source_path,
        precomputed_file_paths=precomputed_file_paths,
        deadline_monotonic=deadline_monotonic,
        deadline_hit=deadline_hit,
        unreadable_hit=unreadable_hit,
    )
    git_cochanges = _bounded_git_cochanges(
        validation_root, source_path, deadline_monotonic=deadline_monotonic
    )

    seen_paths: set[str] = set()
    ranked: list[tuple[int, float, int, str]] = []
    for current in candidate_files:
        if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
            if deadline_hit is not None:
                deadline_hit.hit = True
            break
        if not repo_map_mod._is_test_file(current):
            continue
        resolved = repo_map_mod._resolved_input_path(current)
        if resolved == source_path:
            continue
        current_path = str(resolved)
        if current_path in seen_paths:
            continue
        seen_paths.add(current_path)
        suffix = current.suffix.lower()
        if suffix in _JS_TS_SUFFIXES and not repo_map_mod._javascript_test_file_uses_node_test(
            current_path
        ):
            continue
        if suffix not in _JS_TS_SUFFIXES and suffix != ".py":
            continue

        path_score = repo_map_mod._score_file_path(current_path, terms)
        pattern_bonus = repo_map_mod._framework_test_pattern_bonus(
            current_path, terms, raw_query=query
        )
        tier, conf, tie_score = _classify_test_evidence(
            test_path=current_path,
            primary_file=str(source_path),
            primary_symbol_name=primary_symbol_name,
            query=query,
            repo_map_mod=repo_map_mod,
            git_cochanges=git_cochanges,
            path_score=path_score + pattern_bonus,
        )
        if tier == 4 and (path_score + pattern_bonus) <= 0:
            continue
        ranked.append((5 - tier, conf, tie_score, current_path))

    ranked.sort(key=lambda item: (-item[0], -item[1], -item[2], item[3]))
    return [path for _, _, _, path in ranked[: max(1, limit)]]
