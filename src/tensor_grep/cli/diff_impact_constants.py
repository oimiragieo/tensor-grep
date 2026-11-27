"""Constant-aware symbol extraction and reference blast-radius helpers for diff-impact."""

from __future__ import annotations

import ast
import posixpath
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from tensor_grep.cli import repo_map
from tensor_grep.cli.repo_map_test_paths import _is_test_file
from tensor_grep.core.lang_suffixes import JS_TS_SUFFIXES as _JS_TS_SUFFIXES

_PY_SUFFIXES = frozenset({".py", ".pyi"})

_JS_TS_CONST_RE = re.compile(
    r"^(?:export\s+(?:default\s+)?)?(?:declare\s+)?const\s+([A-Za-z_$][A-Za-z0-9_$]*)\b"
)
_JS_TS_CONST_HEAD_RE = re.compile(r"^(?:export\s+(?:default\s+)?)?(?:declare\s+)?const\s*$")
_RUST_CONST_RE = re.compile(
    r"^(?:pub(?:\s*\([^)]*\))?\s+)?(?:const|static(?:\s+mut)?)\s+([A-Za-z_][A-Za-z0-9_]*)\s*:"
)
_RUST_CONST_HEAD_RE = re.compile(r"^(?:pub(?:\s*\([^)]*\))?\s+)?(?:const|static(?:\s+mut)?)\s*$")
_GO_SINGLE_CONST_RE = re.compile(r"^(?:const|var)\s+([A-Za-z_][A-Za-z0-9_]*)\b(?!\s*\()")
_GO_CONST_HEAD_RE = re.compile(r"^(?:const|var)\s*$")
_GO_BLOCK_START_RE = re.compile(r"^(?:const|var)\s*\(")
_GO_BLOCK_ITEM_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\b")


_DRIVE_RE = re.compile(r"^[A-Za-z]:")
_JS_TS_CONT_END_OPS = ("=", "+", "-", "*", "/", "%", "&", "|", "^", "?", ":", ",", ".", "=>")
_JS_TS_CONT_START_RE = re.compile(r"^(?:\?\.|[.\[(+\-*/?:]|&&|\|\||\?\?)")
_GO_CONT_END_OPS = ("=", "+", "-", "*", "/", "%", "&", "|", "^", ",", "(", "{", "[", "&&", "||")

_JS_TS_IMPORT_BLOCK_RE = re.compile(
    r"\b(?:import|export)\s+(?:type\s+)?\{([^}]*)\}\s*from\s*['\"]([^'\"]+)['\"]",
    re.DOTALL,
)
_JS_TS_REQUIRE_BLOCK_RE = re.compile(
    r"\b(?:const|let|var)\s*\{([^}]*)\}\s*=\s*require\s*\(\s*['\"]([^'\"]+)['\"]\s*\)",
    re.DOTALL,
)
_RUST_USE_RE = re.compile(r"^\s*(?:pub(?:\s*\([^)]*\))?\s+)?use\s+([^;]+);", re.MULTILINE)


def _const_record(name: str, start_line: int, end_line: int | None = None) -> dict[str, Any]:
    end = start_line if end_line is None else end_line
    return {
        "name": name,
        "kind": "constant",
        "line": start_line,
        "start_line": start_line,
        "end_line": end,
    }


def _scan_statement_end_line(
    lines: list[str],
    start_idx: int,
    *,
    require_semicolon: bool,
    allow_backtick: bool,
    rust_lifetimes: bool = False,
    go_style: bool = False,
) -> int:
    """Scan forward from 0-based `start_idx` to find the 1-based end line of a declaration."""
    depth = 0
    in_block_comment = False
    in_backtick = False
    n_lines = len(lines)

    for idx in range(start_idx, n_lines):
        line = lines[idx]
        i = 0
        n = len(line)
        in_quote: str | None = None
        last_code_tokens: list[str] = []

        while i < n:
            ch = line[i]
            nxt = line[i + 1] if i + 1 < n else ""

            if in_block_comment:
                if ch == "*" and nxt == "/":
                    in_block_comment = False
                    i += 2
                    continue
                i += 1
                continue

            if in_backtick:
                if not go_style and ch == "\\":
                    i += 2
                    continue
                if ch == "`":
                    in_backtick = False
                i += 1
                continue

            if in_quote is not None:
                if ch == "\\":
                    i += 2
                    continue
                if ch == in_quote:
                    in_quote = None
                i += 1
                continue

            if ch == "/" and nxt == "/":
                break
            if ch == "/" and nxt == "*":
                in_block_comment = True
                i += 2
                continue
            if ch in ('"', "'"):
                if (
                    rust_lifetimes
                    and ch == "'"
                    and not (nxt == "\\" or (i + 2 < n and line[i + 2] == "'"))
                ):
                    i += 1
                    continue
                in_quote = ch
                last_code_tokens.append(ch)
                i += 1
                continue
            if allow_backtick and ch == "`":
                in_backtick = True
                last_code_tokens.append(ch)
                i += 1
                continue

            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth = max(0, depth - 1)
            elif ch == ";" and depth == 0:
                return idx + 1

            if not ch.isspace():
                last_code_tokens.append(ch)
            i += 1

        if depth == 0 and not in_block_comment and not in_backtick and not require_semicolon:
            tail = "".join(last_code_tokens[-2:]) if last_code_tokens else ""
            last_ch = last_code_tokens[-1] if last_code_tokens else ""
            if go_style:
                if tail in _GO_CONT_END_OPS or last_ch in _GO_CONT_END_OPS:
                    continue
                return idx + 1
            if tail in _JS_TS_CONT_END_OPS or last_ch in _JS_TS_CONT_END_OPS:
                continue
            if idx + 1 < n_lines:
                nxt_stripped = lines[idx + 1].lstrip()
                if nxt_stripped and _JS_TS_CONT_START_RE.match(nxt_stripped):
                    continue
            return idx + 1

    return n_lines


def _extract_python_constants(text: str) -> list[dict[str, Any]]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    found: list[dict[str, Any]] = []
    for stmt in tree.body:
        if isinstance(stmt, ast.Assign):
            end_line = int(getattr(stmt, "end_lineno", stmt.lineno) or stmt.lineno)
            for target in stmt.targets:
                if isinstance(target, ast.Name) and target.id != "_":
                    found.append(_const_record(target.id, int(stmt.lineno), end_line))
        elif isinstance(stmt, ast.AnnAssign):
            if (
                isinstance(stmt.target, ast.Name)
                and stmt.target.id != "_"
                and stmt.value is not None
            ):
                end_line = int(getattr(stmt, "end_lineno", stmt.lineno) or stmt.lineno)
                found.append(_const_record(stmt.target.id, int(stmt.lineno), end_line))
    return found


def _match_line_or_next(
    lines: list[str],
    idx: int,
    full_re: re.Pattern[str],
    head_re: re.Pattern[str],
) -> tuple[re.Match[str], int] | None:
    line = lines[idx]
    match = full_re.match(line)
    if match is not None:
        return match, idx
    if idx + 1 < len(lines) and head_re.match(line):
        next_match = full_re.match(f"{line.rstrip()} {lines[idx + 1].lstrip()}")
        if next_match is not None:
            return next_match, idx + 1
    return None


def _extract_regex_constants(text: str, suffix: str) -> list[dict[str, Any]]:
    lines = text.splitlines()
    found: list[dict[str, Any]] = []
    n_lines = len(lines)
    idx = 0
    if suffix in _JS_TS_SUFFIXES:
        while idx < n_lines:
            matched = _match_line_or_next(lines, idx, _JS_TS_CONST_RE, _JS_TS_CONST_HEAD_RE)
            if matched is not None:
                match, scan_idx = matched
                end_line = _scan_statement_end_line(
                    lines, scan_idx, require_semicolon=False, allow_backtick=True
                )
                found.append(_const_record(match.group(1), idx + 1, end_line))
                idx = max(idx + 1, end_line)
                continue
            idx += 1
    elif suffix == ".rs":
        while idx < n_lines:
            matched = _match_line_or_next(lines, idx, _RUST_CONST_RE, _RUST_CONST_HEAD_RE)
            if matched is not None:
                match, scan_idx = matched
                end_line = _scan_statement_end_line(
                    lines,
                    scan_idx,
                    require_semicolon=True,
                    allow_backtick=False,
                    rust_lifetimes=True,
                )
                found.append(_const_record(match.group(1), idx + 1, end_line))
                idx = max(idx + 1, end_line)
                continue
            idx += 1
    elif suffix == ".go":
        in_block = False
        while idx < n_lines:
            line = lines[idx]
            stripped = line.strip()
            if in_block:
                if line.startswith(")"):
                    in_block = False
                    idx += 1
                    continue
                if not stripped or stripped.startswith(("//", "/*")):
                    idx += 1
                    continue
                block_match = _GO_BLOCK_ITEM_RE.match(line)
                if block_match:
                    end_line = _scan_statement_end_line(
                        lines, idx, require_semicolon=False, allow_backtick=True, go_style=True
                    )
                    found.append(_const_record(block_match.group(1), idx + 1, end_line))
                    idx = max(idx + 1, end_line)
                    continue
                idx += 1
                continue
            if _GO_BLOCK_START_RE.match(line):
                if ")" not in line.split("(", 1)[1]:
                    in_block = True
                idx += 1
                continue
            matched = _match_line_or_next(lines, idx, _GO_SINGLE_CONST_RE, _GO_CONST_HEAD_RE)
            if matched is not None:
                match, scan_idx = matched
                end_line = _scan_statement_end_line(
                    lines, scan_idx, require_semicolon=False, allow_backtick=True, go_style=True
                )
                found.append(_const_record(match.group(1), idx + 1, end_line))
                idx = max(idx + 1, end_line)
                continue
            idx += 1
    return found


def extract_constant_symbols(data: bytes, suffix: str) -> list[dict[str, Any]]:
    """Extract module-level constants / top-level assignments from source bytes."""
    norm_suffix = suffix.lower()
    text = data.decode("utf-8", errors="replace")
    if norm_suffix in _PY_SUFFIXES:
        return _extract_python_constants(text)
    if norm_suffix in _JS_TS_SUFFIXES or norm_suffix in {".rs", ".go"}:
        return _extract_regex_constants(text, norm_suffix)
    return []


def merge_constant_symbols(
    symbols: list[dict[str, Any]], data: bytes, suffix: str
) -> list[dict[str, Any]]:
    """Append non-duplicate constant symbols to `symbols`."""
    constants = extract_constant_symbols(data, suffix)
    if not constants:
        return symbols
    seen = {(str(sym.get("name", "")), int(sym.get("line", 0))) for sym in symbols}
    merged = list(symbols)
    for const_sym in constants:
        key = (str(const_sym["name"]), int(const_sym["line"]))
        if key not in seen:
            seen.add(key)
            merged.append(const_sym)
    return merged


def _normalize_rel_path(path_str: str, root: Path) -> str:
    p = Path(path_str)
    if p.is_absolute():
        try:
            p = p.relative_to(root)
        except ValueError:
            pass
    return str(p).replace("\\", "/")


def _enclosing_symbol(file_symbols: list[dict[str, Any]], line: int) -> tuple[str, bool] | None:
    """Return (name, is_callable) for the innermost symbol enclosing `line`."""
    candidates: list[tuple[int, int, str, bool]] = []
    for sym in file_symbols:
        start = int(sym.get("start_line", sym.get("line", 0)))
        end = int(sym.get("end_line", start))
        if start <= line <= end:
            kind = str(sym.get("kind", ""))
            is_callable = kind in {"function", "method", "async_function"}
            candidates.append((
                end - start,
                0 if is_callable else 1,
                str(sym.get("name", "")),
                is_callable,
            ))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1], item[2]))
    _, _, name, is_callable = candidates[0]
    return (name, is_callable) if name else None


def _python_import_matches_origin(
    module: str | None, level: int, rel_file: str, origin_file: str
) -> bool:
    if not origin_file:
        return False
    origin_p = Path(origin_file)
    origin_targets: list[tuple[str, ...]] = [origin_p.with_suffix("").parts]
    if origin_p.stem == "__init__":
        origin_targets.append(origin_p.parent.parts)

    if level > 0:
        rel_dir_parts = Path(rel_file).parent.parts
        up = level - 1
        if up > len(rel_dir_parts):
            return False
        base_parts = rel_dir_parts[: len(rel_dir_parts) - up]
        target_parts = base_parts + (tuple(module.split(".")) if module else ())
        return target_parts in origin_targets

    if not module:
        return False
    mod_parts = tuple(module.split("."))
    for cand in origin_targets:
        if cand == mod_parts:
            return True
        if len(cand) >= 2 and cand[0] in {"src", "lib"} and cand[1:] == mod_parts:
            return True
    return False


def _js_ts_import_matches_origin(spec: str, rel_file: str, origin_file: str) -> bool:
    if not origin_file:
        return False
    origin_p = Path(origin_file)
    origin_targets = {origin_p.with_suffix("").as_posix()}
    if origin_p.stem == "index":
        origin_targets.add(origin_p.parent.as_posix())

    spec_sfx = Path(spec).suffix.lower()
    spec_stem = spec[: -len(spec_sfx)] if spec_sfx in _JS_TS_SUFFIXES else spec
    if spec_stem.startswith("."):
        base_dir = Path(rel_file).parent
        norm_spec = posixpath.normpath((base_dir / spec_stem).as_posix())
        if norm_spec == ".." or norm_spec.startswith("../"):
            return False
        return norm_spec in origin_targets

    clean = spec_stem[2:] if spec_stem.startswith(("@/", "~/")) else spec_stem
    if clean in origin_targets:
        return True
    return any(
        t.startswith(("src/", "lib/")) and t.split("/", 1)[1] == clean for t in origin_targets
    )


def _rust_use_matches_origin(clause: str, rel_file: str, origin_file: str) -> bool:
    if not origin_file:
        return False
    origin_parts = Path(origin_file).with_suffix("").parts
    if origin_parts and origin_parts[0] == "src":
        origin_parts = origin_parts[1:]
    if origin_parts and origin_parts[-1] in {"mod", "lib", "main"}:
        origin_parts = origin_parts[:-1]

    head = clause.split("::{", 1)[0].strip()
    parts = [p.strip() for p in head.split("::") if p.strip()]
    if not parts:
        return False
    if "::{" not in clause:
        parts = parts[:-1]
    if parts and parts[0] == "crate":
        return tuple(parts[1:]) == origin_parts
    if parts and parts[0] in {"self", "super"}:
        rel_parts = list(Path(rel_file).with_suffix("").parts)
        if rel_parts and rel_parts[0] == "src":
            rel_parts = rel_parts[1:]
        if rel_parts:
            rel_parts = rel_parts[:-1]
        for seg in parts:
            if seg == "self":
                continue
            if seg == "super":
                if not rel_parts:
                    return False
                rel_parts.pop()
            else:
                rel_parts.append(seg)
        return tuple(rel_parts) == origin_parts
    return tuple(parts) == origin_parts or (
        len(parts) > len(origin_parts) and tuple(parts[1:]) == origin_parts
    )


def _resolve_constant_bindings(
    text: str,
    suffix: str,
    symbol_name: str,
    *,
    origin_file: str = "",
    rel_file: str = "",
) -> tuple[set[str], set[str], bool]:
    """Return (aliases, bound_names, parse_failed) for `symbol_name` in `text`."""
    aliases: set[str] = set()
    bound_names: set[str] = set()
    if suffix in _PY_SUFFIXES:
        try:
            tree = ast.parse(text)
        except SyntaxError:
            return aliases, bound_names, True
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if not _python_import_matches_origin(
                    node.module, node.level, rel_file, origin_file
                ):
                    continue
                for alias in node.names:
                    if alias.name == symbol_name:
                        local_name = alias.asname or alias.name
                        bound_names.add(local_name)
                        if alias.asname and alias.asname != symbol_name:
                            aliases.add(alias.asname)
        return aliases, bound_names, False

    if suffix in _JS_TS_SUFFIXES:
        for block_re, sep in ((_JS_TS_IMPORT_BLOCK_RE, "as"), (_JS_TS_REQUIRE_BLOCK_RE, ":")):
            for match in block_re.finditer(text):
                if not _js_ts_import_matches_origin(match.group(2), rel_file, origin_file):
                    continue
                for raw_item in match.group(1).split(","):
                    item = re.sub(r"//.*", "", raw_item).strip()
                    if item.startswith("type "):
                        item = item[5:].strip()
                    if not item:
                        continue
                    if sep == "as":
                        alias_m = re.match(
                            r"^([A-Za-z_$][A-Za-z0-9_$]*)\s+as\s+([A-Za-z_$][A-Za-z0-9_$]*)$",
                            item,
                        )
                    else:
                        alias_m = re.match(
                            r"^([A-Za-z_$][A-Za-z0-9_$]*)\s*:\s*([A-Za-z_$][A-Za-z0-9_$]*)$",
                            item,
                        )
                    if alias_m:
                        if alias_m.group(1) == symbol_name:
                            local_name = alias_m.group(2)
                            bound_names.add(local_name)
                            if local_name != symbol_name:
                                aliases.add(local_name)
                    elif item == symbol_name:
                        bound_names.add(symbol_name)
        return aliases, bound_names, False

    if suffix == ".rs":
        escaped = re.escape(symbol_name)
        alias_re = re.compile(rf"(?:\b|::){escaped}\s+as\s+([A-Za-z_][A-Za-z0-9_]*)")
        direct_re = re.compile(rf"(?:\b|::){escaped}\b")
        for match in _RUST_USE_RE.finditer(text):
            clause = match.group(1)
            if not _rust_use_matches_origin(clause, rel_file, origin_file):
                continue
            for am in alias_re.finditer(clause):
                local_name = am.group(1)
                bound_names.add(local_name)
                if local_name != symbol_name:
                    aliases.add(local_name)
            if direct_re.search(clause):
                bound_names.add(symbol_name)
        return aliases, bound_names, False

    return aliases, bound_names, False


def resolve_constant_blast_radius(
    repo_map_payload: dict[str, Any],
    symbol: dict[str, Any],
    *,
    root: Path,
    max_depth: int = 3,
    deadline_monotonic: float | None = None,
) -> dict[str, Any]:
    """Resolve references, enclosing callers, transitive callers, and tests for a constant."""
    name = str(symbol.get("name", ""))
    origin_file = _normalize_rel_path(str(symbol.get("file", "")), root)
    origin_line = int(symbol.get("line", 0))

    symbols_by_file: dict[str, list[dict[str, Any]]] = {}
    for sym in repo_map_payload.get("symbols", []):
        if isinstance(sym, dict):
            rel = _normalize_rel_path(str(sym.get("file", "")), root)
            symbols_by_file.setdefault(rel, []).append(sym)

    candidate_paths: list[str] = []
    seen_paths: set[str] = set()
    for raw_path in [*repo_map_payload.get("files", []), *repo_map_payload.get("tests", [])]:
        s = str(raw_path)
        norm = _normalize_rel_path(s, root)
        if norm not in seen_paths:
            seen_paths.add(norm)
            candidate_paths.append(s)

    callers: list[dict[str, Any]] = []
    seen_caller_keys: set[tuple[str, str, int]] = set()
    affected_files: set[str] = {origin_file} if origin_file else set()
    tests: set[str] = set()
    enclosing_functions: set[str] = set()
    partial = False
    constant_resolution_incomplete = False
    resolution_gaps_count = 0

    for raw_path in candidate_paths:
        if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
            partial = True
            constant_resolution_incomplete = True
            break
        file_path = Path(raw_path)
        if not file_path.is_absolute():
            file_path = root / file_path
        try:
            if not repo_map._file_may_contain_literal_symbol(file_path, name):
                continue
            raw_bytes = file_path.read_bytes()
        except (OSError, SyntaxError):
            resolution_gaps_count += 1
            constant_resolution_incomplete = True
            partial = True
            continue
        if b"\0" in raw_bytes[:8192]:
            continue
        text = raw_bytes.decode("utf-8", errors="replace")
        rel_file = _normalize_rel_path(str(file_path), root)
        aliases, bound_names, parse_failed = _resolve_constant_bindings(
            text,
            file_path.suffix.lower(),
            name,
            origin_file=origin_file,
            rel_file=rel_file,
        )
        if parse_failed:
            resolution_gaps_count += 1
            constant_resolution_incomplete = True
            partial = True
            continue
        if rel_file == origin_file:
            bound_names.add(name)
        file_syms = symbols_by_file.get(rel_file, [])
        file_has_usage = False
        target_names = [
            *sorted(bound_names),
            *(t for t in (name, *sorted(aliases)) if t not in bound_names),
        ]
        for target_name in target_names:
            try:
                refs, _ = repo_map._references_and_calls_for_path(file_path, target_name, root)
                if (
                    not refs
                    and file_path.suffix.lower() in _JS_TS_SUFFIXES
                    and repo_map._parsed_source_and_tree(str(file_path)) is None
                ):
                    refs, _ = repo_map._regex_references_and_calls(file_path, target_name)
            except (OSError, SyntaxError, ValueError, RuntimeError):
                resolution_gaps_count += 1
                constant_resolution_incomplete = True
                partial = True
                break
            for ref in refs:
                ref_line = int(ref.get("line", 0))
                if rel_file == origin_file and ref_line == origin_line:
                    continue
                is_bound = target_name in bound_names or (
                    target_name == name and bool(aliases) and ref.get("resolution") is not None
                )
                provenance = "binding_match" if is_bound else "heuristic_name_match"
                file_has_usage = True
                enclosing = _enclosing_symbol(file_syms, ref_line)
                if enclosing is not None:
                    enclosing_name, is_callable = enclosing
                    if is_callable:
                        enclosing_functions.add(enclosing_name)
                else:
                    enclosing_name = f"<module:{Path(rel_file).stem}>"
                key = (rel_file, enclosing_name, ref_line)
                if key not in seen_caller_keys:
                    seen_caller_keys.add(key)
                    callers.append({
                        "file": rel_file,
                        "caller": enclosing_name,
                        "name": enclosing_name,
                        "line": ref_line,
                        "symbol": name,
                        "kind": "constant_reference",
                        "ref_kind": str(ref.get("ref_kind", "value")),
                        "provenance": provenance,
                    })
        if file_has_usage:
            affected_files.add(rel_file)
            if _is_test_file(Path(rel_file)):
                tests.add(rel_file)

    sub_scores: list[float] = []
    if max_depth > 0:
        for fn_name in sorted(enclosing_functions):
            if deadline_monotonic is not None and time.monotonic() >= deadline_monotonic:
                partial = True
                constant_resolution_incomplete = True
                break
            sub_radius = repo_map.build_symbol_blast_radius_from_map(
                repo_map_payload,
                fn_name,
                max_depth=max(0, max_depth - 1),
                deadline_monotonic=deadline_monotonic,
            )
            unrelated_coverage_gap = False
            if (
                sub_radius.get("result_incomplete")
                and sub_radius.get("incomplete_reason_class") == "coverage_gap"
                and not sub_radius.get("no_match")
                and not sub_radius.get("partial")
                and not sub_radius.get("incomplete")
            ):
                gap_rows = sub_radius.get("resolution_gaps")
                if isinstance(gap_rows, list) and gap_rows:
                    unrelated_coverage_gap = True
                    for gap in gap_rows:
                        if not isinstance(gap, dict):
                            unrelated_coverage_gap = False
                            break
                        sample = gap.get("files_sample")
                        affected_count = int(gap.get("files_affected", 0) or 0)
                        if not isinstance(sample, list) or affected_count > len(sample):
                            unrelated_coverage_gap = False
                            break
                        for rel_gap_file in sample:
                            gap_path = Path(str(rel_gap_file))
                            if not gap_path.is_absolute():
                                gap_path = root / gap_path
                            try:
                                if repo_map._file_may_contain_literal_symbol(gap_path, fn_name):
                                    unrelated_coverage_gap = False
                                    break
                            except OSError:
                                unrelated_coverage_gap = False
                                break
                        if not unrelated_coverage_gap:
                            break
            if not unrelated_coverage_gap and (
                sub_radius.get("no_match")
                or sub_radius.get("partial")
                or sub_radius.get("result_incomplete")
                or sub_radius.get("incomplete")
            ):
                partial = True
                constant_resolution_incomplete = True
                resolution_gaps_count += 1
            for c in sub_radius.get("callers", []):
                c_file = _normalize_rel_path(str(c.get("file", "")), root)
                c_sym = str(c.get("caller", c.get("name", "")))
                c_line = int(c.get("line", 0))
                key = (c_file, c_sym, c_line)
                if key not in seen_caller_keys:
                    seen_caller_keys.add(key)
                    c_copy = dict(c)
                    c_copy["file"] = c_file
                    callers.append(c_copy)
            for af in sub_radius.get("affected_files", []):
                affected_files.add(_normalize_rel_path(str(af), root))
            for t in sub_radius.get("tests", []):
                tests.add(_normalize_rel_path(str(t), root))
            sub_scores.append(float(sub_radius.get("blast_radius_score", 0.0)))

    if callers or len(affected_files) > (1 if origin_file else 0):
        direct_score = min(1.0, round(0.1 * len(callers) + 0.05 * len(affected_files), 3))
        score = max([direct_score, *sub_scores])
    else:
        score = 0.0

    return {
        "callers": callers,
        "affected_files": sorted(affected_files),
        "tests": sorted(tests),
        "blast_radius_score": score,
        "partial": partial,
        "constant_resolution_incomplete": constant_resolution_incomplete,
        "resolution_gaps_count": resolution_gaps_count,
    }


def _is_confined_relative_ref(ref: str) -> bool:
    """Return True iff `ref` is a lexically confined relative path beneath repo root."""
    if not ref or "\x00" in ref:
        return False
    if ref[0] in "/\\" or _DRIVE_RE.match(ref):
        return False
    segments = re.split(r"[\\/]", ref)
    if ".." in segments:
        return False
    return not Path(ref).is_absolute()


def record_constant_uncertainty(
    sym_radius: dict[str, Any],
    downgrade_reasons: list[str],
    partial_reasons: list[str],
) -> tuple[bool, int]:
    """Record constant resolution uncertainty into downgrade/partial reason lists."""
    gaps = int(sym_radius.get("resolution_gaps_count", 0))
    if not sym_radius.get("constant_resolution_incomplete"):
        return False, gaps
    if "constant_resolution_incomplete" not in downgrade_reasons:
        downgrade_reasons.append("constant_resolution_incomplete")
        partial_reasons.append("constant_resolution_incomplete")
    return True, gaps


def constant_uncertainty_fields(
    changed_symbols: list[dict[str, Any]],
    constant_resolution_incomplete: bool,
    resolution_gaps_count: int,
) -> dict[str, Any]:
    """Return additive constant-resolution uncertainty fields when constants were analyzed."""
    if (
        constant_resolution_incomplete
        or resolution_gaps_count
        or any(s.get("kind") == "constant" for s in changed_symbols)
    ):
        return {
            "constant_resolution_incomplete": constant_resolution_incomplete,
            "resolution_gaps_count": resolution_gaps_count,
        }
    return {}


def check_directory_ref(
    ref: str,
    root: Path,
    *,
    deadline_monotonic: float | None = None,
    rev_parse_returncode: Callable[..., int | None] | None = None,
) -> str | None:
    """Return an actionable error message if `ref` is an existing directory, not a git revision."""
    if not _is_confined_relative_ref(ref):
        return None
    try:
        candidate = root / ref
        is_dir = candidate.resolve().is_relative_to(root.resolve()) and candidate.is_dir()
    except OSError:
        is_dir = False
    if not is_dir:
        return None
    if rev_parse_returncode is None:
        from tensor_grep.cli.diff_impact import _git_rev_parse_returncode

        rev_parse_returncode = _git_rev_parse_returncode
    rc = rev_parse_returncode(
        root,
        ["--verify", "--quiet", "--end-of-options", ref],
        deadline_monotonic=deadline_monotonic,
    )
    if rc is None or rc == 0:
        return None
    return (
        f"{ref!r} is a directory path, not a git revision; pass --path or a git ref such as HEAD~1"
    )


def format_git_diff_error(stderr: bytes | str | None, *, ref: str | None, root: Path) -> str:
    """Decode git diff stderr and append a directory-ref hint for confined directories."""
    from tensor_grep.cli.subprocess_policy import decode_diagnostic_output

    msg = decode_diagnostic_output(stderr).strip()[:500]
    if ref is None or "\x00" in ref or ref.startswith(("\\\\", "//")):
        return msg
    try:
        candidate = Path(ref) if Path(ref).is_absolute() else root / ref
        if candidate.resolve().is_relative_to(root.resolve()) and candidate.is_dir():
            return (
                f"{msg} ({ref!r} is a directory path, not a git revision; "
                "pass --path or a git ref such as HEAD~1)"
            ).strip()
    except OSError:
        pass
    return msg
