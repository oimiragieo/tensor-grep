"""`tg codemap`: a persisted, browsable folder->file->symbol code map (lean index + per-folder
drill-down pages), built ENTIRELY on top of the existing `repo_map.build_repo_map()` extraction.

Positioning (no overlap with sibling commands): `tg orient` is a ranked, bounded, ephemeral
capsule; `tg map` is a raw JSON dump; `tg codemap` is an exhaustive, persisted, browsable
inventory meant to be committed/read like docs. It never re-implements AST/tree-sitter parsing --
that IS the point: a from-scratch code-map generator would duplicate `build_repo_map`'s extraction,
so this module formats that extraction's output instead. Every enrichment here (signature,
docstring-first-sentence, Class.method attribution, folder blurbs) is a FORMATTING pass over data
`build_repo_map` already computed, or a read of the already-content-addressed-cached AST
(`repo_map._cached_ast_parse`) -- never a second, independent `ast.parse`/tree-sitter parse, and
never a new field on the shared `_symbol_record` payload (that payload also backs `tg map`/`tg
agent`/`tg orient`/`tg context` and is pinned by their own contract tests; enrichment here is
joined on ``(file, start_line)`` and stays local to this module).

Freshness is a CORRECTNESS requirement, not a nicety: every generated map is stamped with a dual
oracle (git revision identity + a content-addressed tree manifest hash), and `tg codemap --check`
is a READ-ONLY (no re-parse) verifier that fails CLOSED -- an unverifiable map reads as stale, a
partial map never reads as fresh, and any drift (edited/added/removed file, or a changed git
commit/dirty-state) flips the check from fresh to stale.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import subprocess
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from tensor_grep.cli import evidence_receipt as _evidence_receipt
from tensor_grep.cli import lang_registry
from tensor_grep.cli import orient_capsule as _orient_capsule
from tensor_grep.cli import repo_map as _repo_map
from tensor_grep.cli._index_lock import replace_with_retry
from tensor_grep.cli.subprocess_policy import configured_git_timeout_seconds, run_subprocess

# Mirrors inventory's/docs-coverage's 50000 default (NOT map's 512 or agent's 2000 -- codemap is
# an exhaustive inventory, not an agent-context budget). Kept as a real module constant (unlike
# main.py's CLI option, which literal-duplicates the number to keep the heavy import lazy -- the
# established `map`/`inventory` pattern; a routing-parity/contract test is not needed for a private
# default since nothing else reads it).
DEFAULT_MAX_REPO_FILES = 50_000
DEFAULT_MAX_SYMBOLS_PER_FILE = 50

# CLI-ONLY default (#153): a huge multi-root workspace can make `tg codemap` hang for ~90s because
# the --deadline CLI option used to default to None (unbounded). This constant is read by main.py's
# --deadline option (literal-mirrored there, like DEFAULT_MAX_REPO_FILES above, to keep the heavy
# codemap import lazy) and pinned against it by a guard test. It intentionally does NOT change
# build_codemap's own `deadline_seconds: float | None = None` signature default below -- a direct
# library call stays unbounded by default; only the CLI front door is agent-loop-safe by default.
DEFAULT_CLI_DEADLINE_SECONDS = 60.0

_COVERAGE_SCHEMA_VERSION = 1
_COVERAGE_FILENAME = "_coverage.json"

_DOCSTRING_SENTENCE_LIMIT = 220
_SOURCE_LINE_SIGNATURE_LIMIT = 140
_ROLE_CELL_LIMIT = 100

_LANGUAGE_LABELS = {
    "python": "Python",
    "javascript": "JavaScript",
    "typescript": "TypeScript",
    "rust": "Rust",
    "go": "Go",
}
_NON_CODE_LANGUAGE_LABELS = {
    ".md": "Markdown",
    ".markdown": "Markdown",
    ".rst": "reStructuredText",
    ".txt": "Text",
    ".json": "JSON",
    ".yaml": "YAML",
    ".yml": "YAML",
    ".toml": "TOML",
    ".ini": "INI",
    ".cfg": "Config",
    ".adoc": "AsciiDoc",
}

_SENTENCE_END_RE = re.compile(r"[.!?](?:\s|$)")
_SLUG_UNSAFE_RE = re.compile(r"[^A-Za-z0-9_.-]+")


# ---------------------------------------------------------------------------
# Small, pure formatting helpers (no I/O)
# ---------------------------------------------------------------------------


def _truncate_ascii(text: str, limit: int) -> str:
    """Truncate to `limit` chars using an ASCII `...` marker (never U+2026 -- the cp1252 Windows
    stdout crash class; AGENTS.md ASCII-only rule)."""
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _first_sentence(text: str | None, *, limit: int = _DOCSTRING_SENTENCE_LIMIT) -> str:
    """First sentence of `text` (whitespace-collapsed), capped at `limit` chars. Returns "" for
    None/empty input -- callers rely on this to render an EMPTY description cell rather than
    fabricate filler (the old script's `_infer_from_name` name-echo anti-pattern this module must
    never reintroduce)."""
    if not text:
        return ""
    normalized = " ".join(text.split())
    if not normalized:
        return ""
    match = _SENTENCE_END_RE.search(normalized)
    sentence = normalized[: match.end()].strip() if match else normalized
    return _truncate_ascii(sentence, limit)


def _folder_slug(folder_rel_posix: str) -> str:
    """Filesystem-safe page filename stem for a repo-relative POSIX folder path. `""`/"."` (files
    living directly at the repo root) get the reserved `_root` slug."""
    if folder_rel_posix in ("", "."):
        return "_root"
    return _SLUG_UNSAFE_RE.sub("_", folder_rel_posix.replace("/", "_"))


def _escape_cell(text: str) -> str:
    """Escape a value for embedding in a GFM table cell: a raw `|` (common in TS union types,
    e.g. `string | number`) would otherwise silently corrupt the table structure."""
    return text.replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def _relative_link(target: Path, *, from_dir: Path) -> str:
    try:
        rel = os.path.relpath(target, start=from_dir)
    except ValueError:
        rel = str(target)
    return Path(rel).as_posix()


def _repo_relative_posix(file_path: str, root: Path) -> str:
    try:
        return Path(file_path).resolve().relative_to(root).as_posix()
    except (OSError, ValueError):
        return Path(file_path).as_posix()


def _revision_exclude_prefixes(out_dir: Path, root: Path) -> list[str]:
    """The single repo-relative POSIX exclude-prefix for `_repo_revision_identity`'s git-dirty
    oracle: the map's own `--out` directory, so regenerating a persisted, committed map never
    reads as a dirty change against itself (the `tg codemap --check` false-positive). Computed
    fresh from the SAME `out_dir` input at BOTH the stamp site (`build_codemap`) and the `--check`
    site (`check_codemap_freshness`) -- symmetric by construction, never hardcoded to the
    `docs/code-map` default (a custom `--out` is excluded too)."""
    return [_repo_relative_posix(str(out_dir), root)]


def _is_under_dir(path: Path, directory: Path) -> bool:
    try:
        path.resolve().relative_to(directory.resolve())
        return True
    except (OSError, ValueError):
        return False


def _language_label(path_str: str) -> str:
    spec = lang_registry.spec_for_path(path_str)
    if spec is not None:
        return _LANGUAGE_LABELS.get(spec.language_id, spec.language_id.capitalize())
    suffix = Path(path_str).suffix.lower()
    return _NON_CODE_LANGUAGE_LABELS.get(suffix, "Text")


# ---------------------------------------------------------------------------
# Python enrichment: FORMATS the already-cached AST -- never a second ast.parse.
# ---------------------------------------------------------------------------


_PyDefNode = ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef


def _iter_python_defs(
    node: ast.AST, *, parent_class: str | None = None
) -> Iterator[tuple[_PyDefNode, str | None]]:
    """Yield (def_node, owning_class_name_or_None) for every ClassDef/FunctionDef/AsyncFunctionDef
    reachable from `node` -- the same exhaustive set `ast.walk` (repo_map's own symbol walker)
    reaches, but additionally tracking DIRECT class-body membership so a method can be attributed
    to its class (`Class.method`). A function nested inside a method (closure) is NOT itself
    attributed as a method of the outer class (`parent_class` resets to None one level in)."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.ClassDef):
            yield child, parent_class
            yield from _iter_python_defs(child, parent_class=child.name)
        elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield child, parent_class
            yield from _iter_python_defs(child, parent_class=None)
        else:
            yield from _iter_python_defs(child, parent_class=parent_class)


def _format_python_args(args_node: ast.arguments) -> str:
    try:
        return ast.unparse(args_node)
    except (ValueError, TypeError, RecursionError):
        # Defensive fallback (names only, no defaults/annotations) -- ast.unparse is expected to
        # handle `arguments` nodes directly, but this never lets a single odd signature crash the
        # whole run.
        names = [a.arg for a in (*args_node.posonlyargs, *args_node.args)]
        if args_node.vararg:
            names.append(f"*{args_node.vararg.arg}")
        names.extend(a.arg for a in args_node.kwonlyargs)
        if args_node.kwarg:
            names.append(f"**{args_node.kwarg.arg}")
        return ", ".join(names)


def _format_python_signature(
    node: ast.FunctionDef | ast.AsyncFunctionDef, *, display_name: str
) -> str:
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    return f"{prefix} {display_name}({_format_python_args(node.args)})"


def _format_python_class_signature(node: ast.ClassDef, *, display_name: str) -> str:
    try:
        bases = [ast.unparse(base) for base in node.bases]
    except (ValueError, TypeError, RecursionError):
        bases = []
    if bases:
        return f"class {display_name}({', '.join(bases)})"
    return f"class {display_name}"


def _python_file_enrichment(path: Path) -> tuple[dict[int, dict[str, Any]], str]:
    """Read `path` the SAME way `repo_map._python_imports_and_symbols` does
    (`path.read_text(encoding="utf-8")`) and reuse the content-addressed `_cached_ast_parse` cache
    (keyed on that exact source text) -- a cache HIT, not a re-parse, for any file already scanned
    by `build_repo_map` earlier in this same process. Returns (enrichment keyed by
    ``node.lineno`` == the symbol's ``start_line``, module purpose first-sentence)."""
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {}, ""
    try:
        tree = _repo_map._cached_ast_parse(source)
    except (SyntaxError, ValueError, RecursionError):
        return {}, ""

    enrichment: dict[int, dict[str, Any]] = {}
    for node, parent_class in _iter_python_defs(tree):
        display_name = f"{parent_class}.{node.name}" if parent_class else node.name
        if isinstance(node, ast.ClassDef):
            signature = _format_python_class_signature(node, display_name=display_name)
        else:
            signature = _format_python_signature(node, display_name=display_name)
        docstring = ast.get_docstring(node)
        enrichment[node.lineno] = {
            "signature": signature,
            "docstring": _first_sentence(docstring),
            "parent_class": parent_class,
        }
    module_purpose = _first_sentence(ast.get_docstring(tree))
    return enrichment, module_purpose


# ---------------------------------------------------------------------------
# JS/TS/Rust enrichment: source-line signature fallback (+ Rust `///` doc comments).
# ---------------------------------------------------------------------------


def _source_line_signature(
    path_str: str, start_line: int, *, limit: int = _SOURCE_LINE_SIGNATURE_LIMIT
) -> str:
    try:
        source = _repo_map._read_source_text_cached(path_str)
    except OSError:
        return ""
    lines = source.splitlines()
    if start_line < 1 or start_line > len(lines):
        return ""
    return _truncate_ascii(lines[start_line - 1].strip(), limit)


def _rust_doc_comment(path_str: str, start_line: int) -> str:
    """Collect the contiguous run of `///` lines immediately preceding `start_line` (a blank or
    non-`///` line breaks the chain), first-sentence it. JS/TS carries no docs in v1 (spec)."""
    try:
        source = _repo_map._read_source_text_cached(path_str)
    except OSError:
        return ""
    lines = source.splitlines()
    doc_lines: list[str] = []
    idx = start_line - 2  # 0-indexed line directly above start_line (start_line is 1-indexed)
    while idx >= 0:
        stripped = lines[idx].strip()
        if not stripped.startswith("///"):
            break
        doc_lines.insert(0, stripped[3:].strip())
        idx -= 1
    return _first_sentence(" ".join(doc_lines)) if doc_lines else ""


def _enrichment_for_file(
    file_path: str, file_symbols: list[dict[str, Any]]
) -> tuple[dict[int, dict[str, Any]], str]:
    """Dispatch by suffix: Python reuses the cached AST (whole-file pass); JS/TS/Rust build a
    per-symbol entry from the already-known `start_line`s via the source-line fallback (no
    extractor of their own -- `file_symbols` already came from `build_repo_map`)."""
    suffix = Path(file_path).suffix.lower()
    if suffix == ".py":
        return _python_file_enrichment(Path(file_path))
    if suffix in _repo_map._RUST_SUFFIXES:
        enrichment = {}
        for symbol in file_symbols:
            start_line = int(symbol.get("start_line", symbol.get("line", 0)) or 0)
            enrichment[start_line] = {
                "signature": _source_line_signature(file_path, start_line),
                "docstring": _rust_doc_comment(file_path, start_line),
                "parent_class": None,
            }
        return enrichment, ""
    if suffix in _repo_map._JS_TS_SUFFIXES:
        enrichment = {}
        for symbol in file_symbols:
            start_line = int(symbol.get("start_line", symbol.get("line", 0)) or 0)
            enrichment[start_line] = {
                "signature": _source_line_signature(file_path, start_line),
                "docstring": "",
                "parent_class": None,
            }
        return enrichment, ""
    return {}, ""


def _first_markdown_heading(path_str: str) -> str:
    try:
        source = _repo_map._read_source_text_cached(path_str)
    except OSError:
        return ""
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
    return ""


def _file_purpose(path_str: str, *, module_purpose: str, language_label: str) -> str:
    """Module docstring first sentence (Python); `.md` first heading; else a generic
    ``"<lang> file"`` -- never the old script's name-echo filler."""
    suffix = Path(path_str).suffix.lower()
    if suffix == ".py" and module_purpose:
        return module_purpose
    if suffix in {".md", ".markdown"}:
        heading = _first_markdown_heading(path_str)
        if heading:
            return heading
    return f"{language_label} file"


# ---------------------------------------------------------------------------
# Folder blurb fallback chain: overlay -> README.md -> __init__.py -> generic.
# Works WITHOUT any migration -- the overlay directory is simply absent until one is authored.
# ---------------------------------------------------------------------------


def _load_enrichment_overlays(out_dir: Path) -> dict[str, str]:
    """Merge every ``<out>/_enrichments/*.json`` file into one folder-relative-POSIX-path -> blurb
    map. Each overlay file is a flat ``{"folder/path": "One-line blurb."}`` object (a string value)
    or, for forward compatibility, ``{"folder/path": {"role": "One-line blurb."}}``. Missing/absent
    directory -> empty dict (this feature must work without the `_enrichments/` migration)."""
    overlays_dir = out_dir / "_enrichments"
    merged: dict[str, str] = {}
    if not overlays_dir.is_dir():
        return merged
    try:
        overlay_paths = sorted(overlays_dir.glob("*.json"))
    except OSError:
        return merged
    for overlay_path in overlay_paths:
        try:
            data = json.loads(overlay_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        for key, value in data.items():
            if isinstance(value, str):
                merged[key] = value
            elif isinstance(value, dict) and isinstance(value.get("role"), str):
                merged[key] = value["role"]
    return merged


def _first_readme_sentence(readme_path: Path) -> str:
    try:
        text = readme_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        return _first_sentence(stripped)
    return ""


def _init_docstring_sentence(init_path: Path) -> str:
    try:
        source = init_path.read_text(encoding="utf-8")
        tree = _repo_map._cached_ast_parse(source)
    except (OSError, UnicodeDecodeError, SyntaxError, ValueError, RecursionError):
        return ""
    return _first_sentence(ast.get_docstring(tree))


def _folder_blurb(folder_rel_posix: str, *, root: Path, overlays: dict[str, str]) -> str:
    if folder_rel_posix in overlays:
        return overlays[folder_rel_posix]
    folder_abs = root if folder_rel_posix in ("", ".") else root / folder_rel_posix
    readme_sentence = _first_readme_sentence(folder_abs / "README.md")
    if readme_sentence:
        return readme_sentence
    init_sentence = _init_docstring_sentence(folder_abs / "__init__.py")
    if init_sentence:
        return init_sentence
    display_name = folder_rel_posix if folder_rel_posix not in ("", ".") else root.name
    return f"Project folder `{display_name}`."


# ---------------------------------------------------------------------------
# Universe construction: self-exclusion (--out/--index) + folder grouping.
# ---------------------------------------------------------------------------


def _excluded_by_output_str(file_str: str, *, out_dir: Path, index_path: Path) -> bool:
    candidate = Path(file_str)
    if _is_under_dir(candidate, out_dir):
        return True
    try:
        return candidate.resolve() == index_path.resolve()
    except OSError:
        return False


def _exclude_output_paths(rm: dict[str, Any], *, out_dir: Path, index_path: Path) -> dict[str, Any]:
    """Post-filter the repo_map payload to drop every file under --out (incl. --index) -- mirrors
    orient_capsule.py's `_apply_ignore_globs` (a proven precedent for this exact post-hoc payload
    filtering). Without this, codemap's OWN previously-written `.md`/`.json` pages would be walked
    back in as new "source files" on the next run (self-invalidation)."""

    def _excluded(file_str: str) -> bool:
        return _excluded_by_output_str(file_str, out_dir=out_dir, index_path=index_path)

    filtered = dict(rm)
    filtered["files"] = [f for f in rm.get("files", []) if not _excluded(str(f))]
    filtered["tests"] = [f for f in rm.get("tests", []) if not _excluded(str(f))]
    filtered["symbols"] = [
        s for s in rm.get("symbols", []) if not _excluded(str(s.get("file", "")))
    ]
    filtered["imports"] = [
        i for i in rm.get("imports", []) if not _excluded(str(i.get("file", "")))
    ]
    return filtered


def _tracked_file_set(root: Path) -> set[str] | None:
    """Resolved absolute paths of every git-tracked file under `root` (`git ls-files -z`), or
    `None` when git is unavailable/errors (not a repo, git missing, timeout). `None` is a distinct
    sentinel from "empty set": callers must degrade to "no intersection possible" (keep
    everything) on `None`, never mistake it for "this repo genuinely tracks zero files"."""
    try:
        result = run_subprocess(
            ["git", "-C", str(root), "ls-files", "-z"],
            timeout_seconds=configured_git_timeout_seconds(),
            capture_output=True,
            text=True,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    tracked: set[str] = set()
    for rel_posix in result.stdout.split("\0"):
        if not rel_posix:
            continue
        try:
            tracked.add(str((root / rel_posix).resolve()))
        except OSError:
            continue
    return tracked


def _exclude_untracked_paths(rm: dict[str, Any], *, root: Path) -> dict[str, Any]:
    """Post-filter the repo_map payload to drop every file `git ls-files` does not track --
    mirrors `_exclude_output_paths`'s exact shape. An untracked/gitignored file (scratch script,
    build artifact, local-only note) is filesystem-real but not part of the project's committed
    surface, and its volatile mtime/existence must never leak into the persisted, browsable
    inventory. Degrades to a no-op (returns `rm` unchanged) when the tracked-file set is
    unavailable (non-git dir, git missing, timeout) -- never crashes, never guesses."""
    tracked = _tracked_file_set(root)
    if tracked is None:
        return rm

    def _is_tracked(file_str: str) -> bool:
        if not file_str:
            return False
        try:
            return str(Path(file_str).resolve()) in tracked
        except OSError:
            return False

    filtered = dict(rm)
    filtered["files"] = [f for f in rm.get("files", []) if _is_tracked(str(f))]
    filtered["tests"] = [f for f in rm.get("tests", []) if _is_tracked(str(f))]
    filtered["symbols"] = [s for s in rm.get("symbols", []) if _is_tracked(str(s.get("file", "")))]
    filtered["imports"] = [i for i in rm.get("imports", []) if _is_tracked(str(i.get("file", "")))]
    return filtered


def _folder_for_file(file_path: str, root: Path) -> str:
    rel = Path(_repo_relative_posix(file_path, root))
    parent = rel.parent
    return "" if str(parent) == "." else parent.as_posix()


def _group_by_folder(universe: list[str], root: Path) -> dict[str, list[str]]:
    folders: dict[str, list[str]] = {}
    for file_path in universe:
        folders.setdefault(_folder_for_file(file_path, root), []).append(file_path)
    return folders


def _self_verify_universe_coverage(universe: list[str], folders: dict[str, list[str]]) -> bool:
    """Every universe file must appear in EXACTLY one folder bucket -- a defensive correctness
    gate (Section 4's partial contract), not a tautology: a grouping bug (or a future refactor)
    that double-counts or drops a file flips this to False, which flips the whole map to
    partial:true rather than silently shipping an incomplete inventory as if it were complete."""
    accounted: set[str] = set()
    for files in folders.values():
        for file_path in files:
            if file_path in accounted:
                return False
            accounted.add(file_path)
    return accounted == set(universe)


# ---------------------------------------------------------------------------
# Walk-only helpers (NO parsing) -- shared by the zero-mapped-file folder census (generation) and
# the freshness re-walk (--check, which must never re-parse a single file).
# ---------------------------------------------------------------------------


def _walk_only_universe(root: Path, *, max_repo_files: int) -> list[str]:
    """The exact file universe `build_repo_map`'s parse loop would consume, computed WITHOUT
    parsing a single file: reuses `build_repo_map`'s own walk (`_iter_repo_files`) and its
    pre-parse suffix/hidden-file gate (`_is_repo_context_file`)."""
    context_root = root if root.is_dir() else root.parent
    try:
        all_files = _repo_map._iter_repo_files(root, max_files=max_repo_files)
    except OSError:
        return []
    return [str(f) for f in all_files if _repo_map._is_repo_context_file(f, context_root)]


def _all_folder_paths(root: Path, *, max_repo_files: int) -> set[str]:
    """Every folder (repo-relative POSIX, "" for repo root) containing >=1 file the walk reaches,
    regardless of mapped-suffix status -- used only to report how many folders were excluded from
    the (mapped-files-only) index table because they hold nothing but unmapped extensions."""
    try:
        all_files = _repo_map._iter_repo_files(root, max_files=max_repo_files)
    except OSError:
        return set()
    folders: set[str] = set()
    for f in all_files:
        try:
            rel = f.resolve().relative_to(root)
        except (OSError, ValueError):
            continue
        parent = rel.parent
        folders.add("" if str(parent) == "." else parent.as_posix())
    return folders


def _tree_manifest_sha256(files: list[str], root: Path) -> str:
    """sha256 over sorted (relpath, size, mtime_ns) of `files` -- the git-independent freshness
    oracle. `files` must already exclude --out/--index (else self-invalidation: writing the map
    changes the map's own freshness stamp)."""
    entries: list[tuple[str, int, int]] = []
    for file_str in files:
        file_path = Path(file_str)
        try:
            stat_result = file_path.stat()
        except OSError:
            continue
        entries.append((
            _repo_relative_posix(file_str, root),
            stat_result.st_size,
            stat_result.st_mtime_ns,
        ))
    entries.sort(key=lambda entry: entry[0])
    canonical = "\n".join(
        f"{relpath}\x00{size}\x00{mtime_ns}" for relpath, size, mtime_ns in entries
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Central files (replaces the old script's tg-specific "hot-path cheat sheet"): reuse orient's
# own composite centrality score -- never a second scoring system.
# ---------------------------------------------------------------------------


def _top_central_files(rm: dict[str, Any], *, limit: int = 10) -> list[tuple[str, float]]:
    code_files, centrality = _orient_capsule._file_centrality_scores(rm)
    if not code_files:
        return []
    ranked = sorted(code_files, key=lambda f: (-centrality[f], f))
    return [(f, round(centrality[f], 6)) for f in ranked[:limit]]


# ---------------------------------------------------------------------------
# Stamp formatting (dual oracle: git revision identity + tree manifest hash).
# ---------------------------------------------------------------------------


def _format_utc_iso(moment: datetime) -> str:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _format_stamp_line(revision: dict[str, Any], now_iso: str) -> str:
    if revision.get("status") == "present":
        sha12 = str(revision.get("commit_sha", ""))[:12]
        dirty_label = "dirty" if revision.get("dirty") else "clean"
        identity = f"{sha12} ({dirty_label})"
    else:
        identity = "no-git (manifest-only)"
    return f"_Map stamp: {identity} generated {now_iso}. Verify: tg codemap --check_"


# ---------------------------------------------------------------------------
# Atomic write (crash-safe: write tmp, then a Windows-hardened os.replace).
# ---------------------------------------------------------------------------


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    tmp_path.write_text(content, encoding="utf-8")
    try:
        replace_with_retry(tmp_path, path)
    except OSError:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _resolve_index_path(out_dir: Path, index: str | Path | None) -> Path:
    if index is None:
        return out_dir / "index.md"
    index_path = Path(index).expanduser()
    if index_path.is_absolute():
        return index_path
    return out_dir / index_path


def _render_folder_page(
    folder_rel_posix: str,
    files: list[str],
    *,
    root: Path,
    symbols_by_file: dict[str, list[dict[str, Any]]],
    max_symbols_per_file: int,
    blurb: str,
    stamp_line: str,
    index_path: Path,
    page_path: Path,
) -> str:
    display_path = folder_rel_posix if folder_rel_posix not in ("", ".") else "."
    lines: list[str] = [f"# Folder: {display_path}", "", stamp_line, ""]
    if blurb:
        lines.append(blurb)
        lines.append("")
    lines.append(f"[Back to index]({_relative_link(index_path, from_dir=page_path.parent)})")
    lines.append("")

    for file_path in files:
        rel_path = _repo_relative_posix(file_path, root)
        basename = Path(file_path).name
        file_symbols = symbols_by_file.get(file_path, [])
        enrichment, module_purpose = _enrichment_for_file(file_path, file_symbols)
        language_label = _language_label(file_path)
        purpose = _file_purpose(
            file_path, module_purpose=module_purpose, language_label=language_label
        )

        lines.append(f"### {basename}")
        lines.append("")
        lines.append(f"- Path: `{rel_path}`")
        lines.append(f"- Language: {language_label}")
        lines.append(f"- Purpose: {_escape_cell(purpose)}")
        lines.append("")

        if file_symbols:
            lines.append("| Kind | Name | Line | Description |")
            lines.append("| --- | --- | --- | --- |")
            shown = file_symbols[:max_symbols_per_file]
            for symbol in shown:
                start_line = int(symbol.get("start_line", symbol.get("line", 0)) or 0)
                info = enrichment.get(start_line, {})
                parent_class = info.get("parent_class")
                name = str(symbol.get("name", ""))
                qualified_name = f"{parent_class}.{name}" if parent_class else name
                signature = str(info.get("signature") or "")
                name_cell = (
                    f"`{_escape_cell(signature)}`" if signature else _escape_cell(qualified_name)
                )
                description = _escape_cell(str(info.get("docstring") or ""))
                kind = _escape_cell(str(symbol.get("kind", "")))
                lines.append(f"| {kind} | {name_cell} | {start_line} | {description} |")
            overflow = len(file_symbols) - len(shown)
            if overflow > 0:
                lines.append("")
                lines.append(f"... +{overflow} more (run: tg defs <name> {rel_path})")
        else:
            lines.append("_No indexed symbols in this file._")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _render_index(
    *,
    root: Path,
    folders: dict[str, list[str]],
    symbols_by_file: dict[str, list[dict[str, Any]]],
    blurbs: dict[str, str],
    central_files: list[tuple[str, float]],
    stamp_line: str,
    coverage: dict[str, Any],
    per_page_tokens: dict[str, int],
    page_paths: dict[str, Path],
    index_path: Path,
) -> str:
    lines: list[str] = [f"# Code Map: {root}", "", stamp_line, ""]

    lines.append("## How to use this map")
    lines.append("")
    lines.append(
        "This is a generated, point-in-time browsable inventory (folders -> files -> symbols). "
        "It can drift from the working tree between generations -- prefer live tg callers/defs "
        "(and tg refs) for authoritative, current symbol data; use this map for orientation and to "
        "find WHERE to look before running a live query. Run `tg codemap --check` to verify this "
        "map is still fresh."
    )
    lines.append("")

    lines.append("## Coverage")
    lines.append("")
    lines.append(f"- Files mapped: {coverage['files_total']}")
    lines.append(f"- Folders mapped: {len(folders)}")
    lines.append(f"- Symbols: {coverage['symbols_total']}")
    lines.append(f"- Partial: {'yes' if coverage['partial'] else 'no'}")
    if coverage.get("partial"):
        lines.append(f"- Remediation: {coverage.get('remediation', '')}")
    lines.append("")

    if central_files:
        lines.append("## Top central files")
        lines.append("")
        lines.append("| File | Score |")
        lines.append("| --- | --- |")
        for file_path, score in central_files:
            rel = _repo_relative_posix(file_path, root)
            lines.append(f"| `{_escape_cell(rel)}` | {score} |")
        lines.append("")

    lines.append("## Exclusions")
    lines.append("")
    lines.append(
        f"- Output directory `{coverage.get('out', '')}` (this map's own generated pages) is "
        "excluded from the file universe and the freshness manifest."
    )
    lines.append(
        f"- Index file `{coverage.get('index', '')}` is excluded from the file universe and the "
        "freshness manifest."
    )
    lines.append(
        "- Only files under tensor-grep's mapped source/doc suffixes are included (see `tg map`); "
        "other extensions are not walked into this inventory."
    )
    zero_file_folders = coverage.get("folders_with_no_mapped_files", 0)
    if zero_file_folders:
        lines.append(
            f"- {zero_file_folders} folder(s) contain only unmapped-extension files and are "
            "omitted from the table below (recorded in _coverage.json)."
        )
    lines.append("")

    lines.append("## Folders")
    lines.append("")
    lines.append("| Path | Role | Map | Files | Symbols | ~Tokens |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for folder in sorted(folders):
        files = folders[folder]
        display_path = folder if folder not in ("", ".") else "."
        role = _truncate_ascii(blurbs.get(folder, ""), _ROLE_CELL_LIMIT)
        page_path = page_paths[folder]
        map_link = f"[{page_path.stem}]({_relative_link(page_path, from_dir=index_path.parent)})"
        symbol_count = sum(len(symbols_by_file.get(f, [])) for f in files)
        tokens = per_page_tokens.get(str(page_path), 0)
        lines.append(
            f"| `{_escape_cell(display_path)}` | {_escape_cell(role)} | {map_link} | "
            f"{len(files)} | {symbol_count} | {tokens} |"
        )
    lines.append("")

    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Orchestration: generation + the read-only freshness check.
# ---------------------------------------------------------------------------


def build_codemap(
    path: str | Path = ".",
    *,
    out: str | Path | None = None,
    index: str | Path | None = None,
    max_repo_files: int = DEFAULT_MAX_REPO_FILES,
    max_symbols_per_file: int = DEFAULT_MAX_SYMBOLS_PER_FILE,
    ignore: tuple[str, ...] = (),
    deadline_seconds: float | None = None,
    _revision_identity: Callable[[Path], dict[str, Any]] | None = None,
    _now: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    """Build + persist the browsable code map. Returns the coverage/summary payload (also written
    to ``<out>/_coverage.json``, minus the ``written_files`` key added only to the return value /
    ``--json`` output). ``_revision_identity``/``_now`` are injectable so callers (tests) can force
    byte-identical output across repeated runs -- default to the real git oracle / real UTC clock.

    ``ignore`` drops matching files (basename or repo-relative path glob, repeatable) before the
    folder grouping -- the same ``orient_capsule._apply_ignore_globs`` helper `tg orient`/`tg
    agent` already use, so the exclusion semantics stay identical across commands. ``deadline_seconds``
    bounds the underlying repo scan's wall-clock time (mirrors ``build_symbol_impact`` et al.): a
    cutoff sets the existing ``partial``/``partial_reason`` fields (``partial_reason="deadline"``)
    instead of inventing a new field, and still returns a valid (partial) result -- never hangs,
    never crashes. Both are additive no-ops at their defaults (``()``/``None``).
    """
    root = Path(path).expanduser().resolve()
    if not root.exists():
        raise FileNotFoundError(f"Path not found: {root}")
    if not root.is_dir():
        raise NotADirectoryError(f"tg codemap requires a directory, got a file: {root}")

    out_dir = Path(out).expanduser().resolve() if out is not None else (root / "docs" / "code-map")
    index_path = _resolve_index_path(out_dir, index).resolve()

    if _revision_identity is not None:
        revision = _revision_identity(root)
    else:
        revision = _evidence_receipt._repo_revision_identity(
            root, exclude_prefixes=_revision_exclude_prefixes(out_dir, root)
        )
    now = _now() if _now is not None else datetime.now(UTC)
    now_iso = _format_utc_iso(now)
    stamp_line = _format_stamp_line(revision, now_iso)

    # moat P0-6 pattern (mirrors build_symbol_impact in repo_map.py): convert the relative
    # --deadline to an ABSOLUTE monotonic timestamp ONCE, then thread it into build_repo_map so a
    # huge tree degrades to a partial result instead of running unbounded.
    deadline_monotonic = _repo_map._deadline_monotonic_from_seconds(deadline_seconds)

    rm = _repo_map.build_repo_map(
        root, max_repo_files=max_repo_files, deadline_monotonic=deadline_monotonic
    )
    rm = _orient_capsule._apply_ignore_globs(rm, ignore)
    rm = _exclude_output_paths(rm, out_dir=out_dir, index_path=index_path)
    rm = _exclude_untracked_paths(rm, root=root)

    universe = sorted(set(rm.get("files", [])) | set(rm.get("tests", [])))

    symbols_by_file: dict[str, list[dict[str, Any]]] = {}
    for symbol in rm.get("symbols", []):
        symbols_by_file.setdefault(str(symbol.get("file", "")), []).append(symbol)

    folders = _group_by_folder(universe, root)
    overlays = _load_enrichment_overlays(out_dir)
    blurbs = {folder: _folder_blurb(folder, root=root, overlays=overlays) for folder in folders}
    page_paths: dict[str, Path] = {
        folder: out_dir / f"{_folder_slug(folder)}.md" for folder in folders
    }
    central_files = _top_central_files(rm)

    scan_limit = rm.get("scan_limit")
    possibly_truncated = bool(isinstance(scan_limit, dict) and scan_limit.get("possibly_truncated"))
    # build_repo_map's own --deadline cutoff signal (moat P0-6): a fired deadline sets rm["partial"]
    # (never present -- not False -- when no deadline was supplied), which is DISTINCT from
    # scan_limit's file-COUNT cap (task #384-#395 deadline program parity with callers/refs/impact).
    deadline_hit = bool(rm.get("partial"))
    self_verify_ok = _self_verify_universe_coverage(universe, folders)
    partial = possibly_truncated or deadline_hit or not self_verify_ok

    partial_reason: str | None = None
    remediation: str | None = None
    if possibly_truncated:
        partial_reason = "scan_limit"
        remediation = (
            "The scan hit --max-repo-files before covering the whole tree. Re-run with a higher "
            "--max-repo-files, or scope PATH to a subdirectory."
        )
    elif deadline_hit:
        partial_reason = "deadline"
        remediation = (
            "The scan hit --deadline before covering the whole tree. Re-run with a higher "
            "--deadline, or scope PATH to a subdirectory."
        )
    elif not self_verify_ok:
        partial_reason = "self_verify"
        remediation = (
            "Internal consistency check failed (a mapped file was not accounted for in exactly one "
            "folder page). Please file a tensor-grep bug with the repository shape that triggered it."
        )

    coverage: dict[str, Any] = {
        "schema_version": _COVERAGE_SCHEMA_VERSION,
        "generated_at": now_iso,
        "tool_version": _evidence_receipt._cli_package_version(),
        "path": str(root),
        "out": str(out_dir),
        "index": str(index_path),
        "revision": revision,
        "tree_manifest_sha256": _tree_manifest_sha256(universe, root),
        "files_total": len(universe),
        "folders_total": len(folders),
        "symbols_total": sum(len(v) for v in symbols_by_file.values()),
        "partial": partial,
        "partial_reason": partial_reason,
        "remediation": remediation,
        "scan_limit": scan_limit,
        "max_repo_files": max_repo_files,
    }

    try:
        all_folders = _all_folder_paths(root, max_repo_files=max_repo_files)
        all_folders = {
            f for f in all_folders if not _is_under_dir(root / f if f else root, out_dir)
        }
        coverage["folders_with_no_mapped_files"] = max(0, len(all_folders) - len(folders))
    except OSError:
        coverage["folders_with_no_mapped_files"] = 0

    written_files: list[Path] = []
    per_page_tokens: dict[str, int] = {}
    for folder, files in folders.items():
        page_path = page_paths[folder]
        page_text = _render_folder_page(
            folder,
            files,
            root=root,
            symbols_by_file=symbols_by_file,
            max_symbols_per_file=max_symbols_per_file,
            blurb=blurbs.get(folder, ""),
            stamp_line=stamp_line,
            index_path=index_path,
            page_path=page_path,
        )
        _atomic_write_text(page_path, page_text)
        written_files.append(page_path)
        per_page_tokens[str(page_path)] = _repo_map._estimate_tokens(page_text)

    index_text = _render_index(
        root=root,
        folders=folders,
        symbols_by_file=symbols_by_file,
        blurbs=blurbs,
        central_files=central_files,
        stamp_line=stamp_line,
        coverage=coverage,
        per_page_tokens=per_page_tokens,
        page_paths=page_paths,
        index_path=index_path,
    )
    _atomic_write_text(index_path, index_text)
    written_files.append(index_path)
    per_page_tokens[str(index_path)] = _repo_map._estimate_tokens(index_text)

    coverage["per_page_token_estimates"] = per_page_tokens

    coverage_path = out_dir / _COVERAGE_FILENAME
    _atomic_write_text(coverage_path, json.dumps(coverage, indent=2, sort_keys=True) + "\n")
    written_files.append(coverage_path)

    result = dict(coverage)
    result["written_files"] = [str(p) for p in written_files]
    return result


def build_codemap_json(path: str | Path = ".", **kwargs: Any) -> str:
    """JSON form of :func:`build_codemap` (the same payload ``--json`` emits on stdout)."""
    return json.dumps(build_codemap(path, **kwargs), indent=2, sort_keys=True)


def check_codemap_freshness(
    path: str | Path = ".",
    *,
    out: str | Path | None = None,
    index: str | Path | None = None,
    max_repo_files: int = DEFAULT_MAX_REPO_FILES,
    _revision_identity: Callable[[Path], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Read-only freshness check (Section 4 of the build spec): NEVER writes, NEVER re-parses a
    single source file. Fails CLOSED on anything unverifiable -- a missing/corrupt/partial stamp,
    or an inability to re-walk the tree, reads as stale, never as fresh. Returns
    ``{"fresh": bool, "reason": str}``."""
    root = Path(path).expanduser().resolve()
    if not root.exists():
        raise FileNotFoundError(f"Path not found: {root}")
    if not root.is_dir():
        raise NotADirectoryError(f"tg codemap requires a directory, got a file: {root}")

    out_dir = Path(out).expanduser().resolve() if out is not None else (root / "docs" / "code-map")
    index_path = _resolve_index_path(out_dir, index).resolve()
    coverage_path = out_dir / _COVERAGE_FILENAME

    try:
        raw = coverage_path.read_text(encoding="utf-8")
    except OSError:
        return {"fresh": False, "reason": f"{_COVERAGE_FILENAME} missing at {coverage_path}"}
    try:
        coverage = json.loads(raw)
    except json.JSONDecodeError:
        return {"fresh": False, "reason": f"{_COVERAGE_FILENAME} is not valid JSON"}
    if not isinstance(coverage, dict):
        return {"fresh": False, "reason": f"{_COVERAGE_FILENAME} is not a JSON object"}

    if coverage.get("partial"):
        return {"fresh": False, "reason": "map was written partial (truncated at generation)"}

    stamped_revision = coverage.get("revision")
    if isinstance(stamped_revision, dict) and stamped_revision.get("status") == "present":
        if _revision_identity is not None:
            live_revision = _revision_identity(root)
        else:
            live_revision = _evidence_receipt._repo_revision_identity(
                root, exclude_prefixes=_revision_exclude_prefixes(out_dir, root)
            )
        if live_revision.get("status") == "present":
            if (
                live_revision.get("commit_sha") == stamped_revision.get("commit_sha")
                and live_revision.get("dirty") == stamped_revision.get("dirty")
                and live_revision.get("dirty_tree_sha256")
                == stamped_revision.get("dirty_tree_sha256")
            ):
                return {"fresh": True, "reason": "git revision identity matches"}
            return {"fresh": False, "reason": "git revision identity changed since generation"}
        # Git was available at generation time but is unavailable now -> fall through to the
        # git-independent tree manifest instead of guessing.

    stamped_manifest = coverage.get("tree_manifest_sha256")
    if not isinstance(stamped_manifest, str) or not stamped_manifest:
        return {"fresh": False, "reason": "no tree manifest hash recorded (unverifiable)"}

    stamped_cap = coverage.get("max_repo_files")
    walk_cap = (
        int(stamped_cap) if isinstance(stamped_cap, int) and stamped_cap > 0 else max_repo_files
    )
    try:
        live_universe = [
            f
            for f in _walk_only_universe(root, max_repo_files=walk_cap)
            if not _excluded_by_output_str(f, out_dir=out_dir, index_path=index_path)
        ]
        live_manifest = _tree_manifest_sha256(sorted(set(live_universe)), root)
    except OSError:
        return {"fresh": False, "reason": "could not re-walk the tree to verify (unverifiable)"}

    if live_manifest == stamped_manifest:
        return {"fresh": True, "reason": "tree manifest matches"}
    return {"fresh": False, "reason": "tree manifest changed since generation"}
