"""``tg docs-coverage`` -- which source files are NOT referenced by any governing doc.

Built from real-AI-use dogfood (v1.19.9), where an AI agent wrote this in ~30 lines and called it
"the most valuable thing in this whole sweep": given a repo, list the source files that no
CLAUDE.md / README / AGENTS.md mentions -- the concrete doc-drift signal for keeping per-directory
agent docs honest. Simpler and higher-precision than a semantic diff (it checks reference EXISTENCE,
not content correctness), so it does not flood with false positives.

Walk-only, reuses the same gitignore-aware walker (``repo_map._iter_repo_files``) as ``inventory`` /
``orient`` so counts stay truth-consistent and vendor/cache/index dirs are excluded for free.
Pure-CPU, no AST parse, no API key.
"""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path
from typing import Any

from tensor_grep.cli.inventory import DEFAULT_MAX_INVENTORY_FILES, _is_test_path
from tensor_grep.cli.repo_map import _iter_repo_files, _looks_like_binary_file

# Path components that are NEVER product source a governing doc documents: tool state (.claude
# worktrees/skills, tg indices), VCS, vendored/third-party trees, and build/cache output. Without
# this, a repo's own .claude/worktrees + benchmarks/external_repos flood the "uncovered" list with
# thousands of files that no CLAUDE.md would ever cite (dogfood 2026-07-03: 79% of the flood).
_EXCLUDED_DIR_PARTS = frozenset({
    ".claude",
    ".git",
    ".hg",
    ".svn",
    ".tensor-grep",
    "_tg_refs",
    ".tg_semantic_index",
    "node_modules",
    ".venv",
    "venv",
    "site-packages",
    "__pycache__",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
    "dist",
    "build",
    "target",
    "external_repos",
    "vendor",
    "third_party",
})

# Files that GOVERN a directory's agent/contributor docs. A source file is "covered" if any of these
# reference it. README* is prefix-matched (README, README.md, readme.rst, ...).
_GOVERNING_DOC_NAMES = frozenset({"claude.md", "agents.md", "gemini.md", "contributing.md"})


def _is_governing_doc(name: str) -> bool:
    lower = name.lower()
    return lower in _GOVERNING_DOC_NAMES or lower.startswith("readme")


def _is_fixture_path(path: Path) -> bool:
    # Test-fixture / sample-corpus trees are scaffolding, not product source a governing doc cites
    # (e.g. benchmarks/bakeoff_fixtures/**). Treat like tests.
    return any(
        ("fixture" in part.lower() or part.lower() in {"testdata", "test_data", "__fixtures__"})
        for part in path.parts
    )


# Source-code suffixes we hold to doc coverage. Config/data/lockfiles are intentionally excluded --
# an agent doc is expected to mention CODE files, not every .json/.lock.
_SOURCE_SUFFIXES = frozenset({
    ".py",
    ".pyi",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".rs",
    ".go",
    ".java",
    ".rb",
    ".php",
    ".c",
    ".cc",
    ".cpp",
    ".cxx",
    ".h",
    ".hpp",
    ".hh",
    ".cs",
    ".swift",
    ".kt",
    ".kts",
    ".scala",
    ".vue",
    ".svelte",
    ".m",
    ".mm",
})

# Per-doc read cap (DoS hardening, mirrors the round-5 directory-scanner / gitignore byte caps): a
# hostile multi-MB doc must not blow memory. 2 MB is far past any real CLAUDE.md.
_MAX_DOC_BYTES = 2_000_000


def _relative_posix(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _human_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{num_bytes} B"


def _uncovered_file_detail(path: Path, root: Path) -> dict[str, Any]:
    """path + size + first non-blank line, for the --fix paste-ready table (bounded reads)."""
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    first_line = ""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for raw in handle:
                stripped = raw.strip()
                if stripped:
                    first_line = stripped[:100]
                    break
    except OSError:
        first_line = ""
    return {"path": _relative_posix(path, root), "size_bytes": int(size), "first_line": first_line}


def _has_excluded_ancestor(file_path: Path, resolved_root: Path) -> bool:
    """True if any directory component BELOW the scan root is a tool-state/vendor/build dir.

    Match against the RELATIVE-to-root parts, never the absolute path: _iter_repo_files returns
    resolved (absolute) paths, so checking `file_path.parts` would exclude the ENTIRE repo whenever
    the checkout itself lives under an ancestor named build/venv/target/... (e.g. a CI path like
    /build/tensor-grep) -> source_files=0 -> coverage_pct=100.0, a silent false-green. Mirrors
    inventory._is_test_path's relative-parts handling.
    """
    try:
        relative_parts = file_path.resolve().relative_to(resolved_root).parts
    except ValueError:
        relative_parts = file_path.parts
    return any(part in _EXCLUDED_DIR_PARTS for part in relative_parts)


def _matches_ignore(rel: str, name: str, ignore: tuple[str, ...]) -> bool:
    """True if a source file should be excluded via --ignore. Matches each glob against BOTH the
    repo-relative posix path (`commands/*/index.js`) and the bare basename (`*.stub.py`), so an
    intentional stub group is easy to silence without re-flagging every run."""
    return any(fnmatch.fnmatch(rel, glob) or fnmatch.fnmatch(name, glob) for glob in ignore)


def build_docs_coverage(
    path: str = ".",
    *,
    max_files: int = DEFAULT_MAX_INVENTORY_FILES,
    include_details: bool = False,
    ignore: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Report which source files are not referenced by any governing doc under ``path``.

    Raises ``FileNotFoundError`` when ``path`` does not exist (fail closed -- a missing path must
    never read as a fully-covered empty repo).
    """
    root = Path(path)
    if not root.exists():
        raise FileNotFoundError(f"docs-coverage path does not exist: {path}")

    # Thread the cap into the walk (bucketed early-stop); ask for +1 to distinguish exactly-max from
    # more-exist for the truncation notice, matching inventory.
    walked = _iter_repo_files(root, max_files=max_files + 1)
    possibly_truncated = False
    truncation_cause: str | None = None
    if len(walked) > max_files:
        walked = walked[:max_files]
        possibly_truncated = True
        truncation_cause = "project-files"

    resolved_root = root.resolve()
    doc_paths: list[Path] = []
    source_paths: list[Path] = []
    for file_path in walked:
        # Tool-state / vendor / build trees are never "documented source" -- skip them entirely so
        # they cannot flood either the doc set or the uncovered set.
        if _has_excluded_ancestor(file_path, resolved_root):
            continue
        if _is_governing_doc(file_path.name):
            doc_paths.append(file_path)
        elif (
            file_path.suffix.lower() in _SOURCE_SUFFIXES
            and not _is_test_path(
                file_path, resolved_root
            )  # a governing doc documents source, not tests
            and not _is_fixture_path(file_path)
            and not _looks_like_binary_file(file_path)
            # --ignore: an intentional stub group is excluded entirely (not counted as uncovered nor
            # dragging coverage_pct). Only pay the relative-path cost when globs were actually given.
            and not (
                ignore
                and _matches_ignore(
                    _relative_posix(file_path, resolved_root), file_path.name, ignore
                )
            )
        ):
            source_paths.append(file_path)

    # Concatenate all governing-doc text once (byte-capped per doc). A source file is COVERED if its
    # repo-relative path OR its basename appears anywhere in that text -- lenient on purpose (a
    # per-directory CLAUDE.md usually cites files by basename), so we under-report gaps rather than
    # flood with false "undocumented" noise.
    doc_texts: list[str] = []
    for doc_path in doc_paths:
        try:
            doc_texts.append(
                doc_path.read_text(encoding="utf-8", errors="replace")[:_MAX_DOC_BYTES]
            )
        except OSError:
            continue
    haystack = "\n".join(doc_texts)

    uncovered_pairs: list[tuple[str, Path]] = []
    covered = 0
    for file_path in source_paths:
        rel = _relative_posix(file_path, resolved_root)
        if rel in haystack or file_path.name in haystack:
            covered += 1
        else:
            uncovered_pairs.append((rel, file_path))
    uncovered_pairs.sort(key=lambda item: item[0])
    uncovered = [rel for rel, _ in uncovered_pairs]

    total = len(source_paths)
    payload: dict[str, Any] = {
        "path": str(resolved_root),
        "totals": {
            "source_files": total,
            "covered": covered,
            "uncovered": len(uncovered),
            "coverage_pct": round(100.0 * covered / total, 1) if total else 100.0,
            "doc_files": len(doc_paths),
        },
        "doc_files": sorted(_relative_posix(d, resolved_root) for d in doc_paths),
        "uncovered_files": uncovered,
        "applied_ignore": list(ignore),
        "scan_limit": {
            "max_files": max_files,
            "possibly_truncated": possibly_truncated,
            "truncation_cause": truncation_cause,
        },
        "coverage": {
            "match": "path-or-basename",
            "governing_docs": "CLAUDE.md/README*/AGENTS.md",
            "excluded": "tests, fixtures, tool-state (.claude/.git/.tensor-grep), vendor, build/cache",
        },
    }
    if include_details:
        # --fix table source: path + size + first non-blank line per uncovered file.
        payload["uncovered_details"] = [
            _uncovered_file_detail(file_path, resolved_root) for _, file_path in uncovered_pairs
        ]
    return payload


def render_docs_coverage_fix_markdown(payload: dict[str, Any]) -> str:
    """Paste-ready Markdown table of undocumented source files (path/size/first line) -- the exact
    manual step an agent otherwise hand-rolls to start closing the gaps (dogfood 1.23.0)."""
    details = payload.get("uncovered_details") or []
    if not details:
        return "All source files are referenced by a governing doc. (Nothing to add.)"
    lines = [
        f"<!-- {len(details)} undocumented source file(s) under {payload['path']} -->",
        "| File | Size | First line |",
        "| --- | ---: | --- |",
    ]
    for detail in details:
        # Escape the Markdown cell delimiter so a `|` in the first line can't break the table.
        first = str(detail.get("first_line", "")).replace("|", "\\|")
        lines.append(f"| `{detail['path']}` | {_human_size(int(detail['size_bytes']))} | {first} |")
    return "\n".join(lines)


def render_docs_coverage_text(payload: dict[str, Any]) -> str:
    """ASCII-only text rendering (typer.echo crashes on non-ASCII on cp1252 Windows consoles)."""
    totals = payload["totals"]
    lines = [
        f"Docs coverage for {payload['path']}",
        f"source_files={totals['source_files']}  covered={totals['covered']}  "
        f"uncovered={totals['uncovered']}  coverage={totals['coverage_pct']}%  "
        f"docs={totals['doc_files']}",
    ]
    if payload["scan_limit"]["possibly_truncated"]:
        lines.append(
            f"[!] truncated at max_files={payload['scan_limit']['max_files']} (project-files)"
        )
    uncovered = payload["uncovered_files"]
    if uncovered:
        lines.append(f"\nUndocumented source files ({len(uncovered)}):")
        lines.extend(f"  {rel}" for rel in uncovered[:200])
        if len(uncovered) > 200:
            lines.append(f"  ... and {len(uncovered) - 200} more (see --json)")
    else:
        lines.append("\nAll source files are referenced by a governing doc.")
    return "\n".join(lines)


# --stale extraction. We ONLY mine deliberate references -- backtick-quoted spans and markdown link
# targets -- never bare prose, and require a path separator + a known extension, to keep precision
# high in doc-heavy repos (dogfood lesson: a naive doc scan floods with illustrative paths). Anchors
# / line suffixes (`foo.py#L10`, `foo.py:10`) are stripped.
_DOC_PATH_RE = re.compile(r"`([^`\n]+)`|\]\(([^)\s]+)\)")
_REFERENCE_SUFFIXES = _SOURCE_SUFFIXES | frozenset({
    ".md",
    ".rst",
    ".txt",
    ".toml",
    ".json",
    ".yaml",
    ".yml",
    ".cfg",
    ".ini",
    ".sh",
    ".ps1",
    ".lock",
})


def _looks_like_repo_path(token: str) -> bool:
    token = token.strip()
    if not token or " " in token or "://" in token or token[:1] in {"#", "@"}:
        return False
    if token.startswith(("http", "mailto:")):
        return False
    if "/" not in token:  # a bare basename is too ambiguous to flag as stale
        return False
    return Path(token).suffix.lower() in _REFERENCE_SUFFIXES


def _extract_doc_path_references(text: str) -> set[str]:
    refs: set[str] = set()
    for match in _DOC_PATH_RE.finditer(text):
        token = (match.group(1) or match.group(2) or "").split("#", 1)[0].split(":", 1)[0]
        token = token.strip().lstrip("./")
        if _looks_like_repo_path(token):
            refs.add(token)
    return refs


def build_docs_stale_references(
    path: str = ".", *, max_files: int = DEFAULT_MAX_INVENTORY_FILES
) -> dict[str, Any]:
    """Inverse of coverage: governing-doc references to files that no longer exist (doc drift the
    other way). A reference is stale only when it resolves to NEITHER the doc's own directory nor the
    repo root AND its parent directory DOES exist -- a moved/deleted file, not a fictional example
    path (precision guard so illustrative snippets don't flood)."""
    root = Path(path)
    if not root.exists():
        raise FileNotFoundError(f"docs-coverage path does not exist: {path}")

    walked = _iter_repo_files(root, max_files=max_files + 1)
    possibly_truncated = len(walked) > max_files
    if possibly_truncated:
        walked = walked[:max_files]
    resolved_root = root.resolve()

    doc_paths = [
        file_path
        for file_path in walked
        if not _has_excluded_ancestor(file_path, resolved_root)
        and _is_governing_doc(file_path.name)
    ]

    stale: list[dict[str, str]] = []
    references_checked = 0
    for doc_path in doc_paths:
        try:
            text = doc_path.read_text(encoding="utf-8", errors="replace")[:_MAX_DOC_BYTES]
        except OSError:
            continue
        doc_rel = _relative_posix(doc_path, resolved_root)
        for reference in sorted(_extract_doc_path_references(text)):
            references_checked += 1
            candidates = (doc_path.parent / reference, resolved_root / reference)
            if any(candidate.exists() for candidate in candidates):
                continue
            if any(candidate.parent.exists() for candidate in candidates):
                stale.append({"doc": doc_rel, "reference": reference})
    stale.sort(key=lambda item: (item["doc"], item["reference"]))

    return {
        "path": str(resolved_root),
        "totals": {
            "doc_files": len(doc_paths),
            "references_checked": references_checked,
            "stale": len(stale),
        },
        "stale_references": stale,
        "scan_limit": {
            "max_files": max_files,
            "possibly_truncated": possibly_truncated,
            "truncation_cause": "project-files" if possibly_truncated else None,
        },
    }


def render_docs_stale_text(payload: dict[str, Any]) -> str:
    """ASCII-only text render of the --stale report."""
    totals = payload["totals"]
    lines = [
        f"Stale doc references for {payload['path']}",
        f"docs={totals['doc_files']}  references_checked={totals['references_checked']}  "
        f"stale={totals['stale']}",
    ]
    stale = payload["stale_references"]
    if stale:
        lines.append(f"\nReferences to files that no longer exist ({len(stale)}):")
        lines.extend(f"  {item['doc']} -> {item['reference']}" for item in stale[:200])
        if len(stale) > 200:
            lines.append(f"  ... and {len(stale) - 200} more (see --json)")
    else:
        lines.append("\nNo stale references found (every cited path still exists).")
    return "\n".join(lines)
