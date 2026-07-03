"""``tg diff-docs`` — detect documentation drift, scoped to the ONE high-precision class.

A finding is emitted only when a doc **code-span** (fenced ``` block or inline `code`) names an
identifier that **no longer resolves** to a symbol tensor-grep parsed from the code. This is the
deterministic, zero-model subset that prior art converges on (Staleguard's default layer); full
signature/semantic drift is deliberately OUT (DocPrism: naive code-doc drift detection is 0.62
precision / 98% flag-rate — noise an agent learns to ignore).

Design (round-4 [e] diff-docs, 3-lens design council 2026-07-03):

* **Scope-gate BEFORE resolution.** ``repo_map["symbols"]`` only holds python/js/ts/rust symbols,
  and ``repo_map._language_for_path`` DEFAULTS unknown extensions to "python" — so a token from a
  Go/Ruby/bash span would resolve to nothing and read as a false "unresolved". Fenced spans are
  gated by their language tag; out-of-scope-language docs are counted in ``coverage``, never
  silently reported as "0 findings = clean" (suppression != absence).
* **Precision denylists.** length floor, CLI flags, language keywords, builtins, tg's own command
  names, and a common-English stoplist are dropped pre-resolution — a single false positive per
  page destroys agent trust.
* **"unresolved" not "removed".** tg has no git history, so it cannot assert a symbol was removed.
* **``docs/PAPER.md`` / historical docs** deliberately preserve failed approaches (AGENTS.md) — their
  references are downgraded to low confidence, never standard findings.
* **Fail closed** on a nonexistent docs path.
"""

from __future__ import annotations

import builtins
import keyword
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from tensor_grep.cli.inventory import _DOC_SUFFIXES
from tensor_grep.cli.repo_map import _iter_repo_files, build_repo_map

DIFF_DOCS_SCHEMA_VERSION = 1
DEFAULT_MAX_DIFF_DOCS_FILES = 50_000
_MIN_TOKEN_LEN = 4

# Fence info-string -> in-scope language (matches what tg's AST layer actually parses into symbols).
_FENCE_LANGUAGE: dict[str, str] = {
    "python": "python",
    "py": "python",
    "python3": "python",
    "javascript": "javascript",
    "js": "javascript",
    "jsx": "javascript",
    "mjs": "javascript",
    "typescript": "typescript",
    "ts": "typescript",
    "tsx": "typescript",
    "rust": "rust",
    "rs": "rust",
}
_IN_SCOPE_LANGUAGES = frozenset({"python", "javascript", "typescript", "rust"})

# Identifier token, dotted paths captured as ONE unit (obj.method, module.Class).
_TOKEN_RE = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*(?:\.[A-Za-z_$][A-Za-z0-9_$]*)*")
_FENCE_RE = re.compile(r"^(`{3,}|~{3,})(.*)$")
_INLINE_RE = re.compile(r"`([^`\n]+?)`")

# tg's own subcommand words collide with generic English AND real commands — never flag bare.
_TG_COMMAND_NAMES = frozenset({
    "run",
    "test",
    "map",
    "search",
    "new",
    "scan",
    "doctor",
    "inventory",
    "agent",
    "source",
})
# Common words that appear in code spans but are almost never a drifted symbol reference.
_COMMON_WORD_STOPLIST = frozenset({
    "data",
    "value",
    "object",
    "file",
    "error",
    "result",
    "config",
    "state",
    "self",
    "this",
    "true",
    "false",
    "null",
    "none",
    "type",
    "name",
    "path",
    "text",
    "line",
    "code",
})
_CURATED_STDLIB = frozenset({"Path", "Optional", "Enum", "Any", "Dict", "List", "Tuple", "Set"})
_LANGUAGE_KEYWORDS = frozenset(keyword.kwlist) | frozenset({
    "function",
    "const",
    "let",
    "var",
    "fn",
    "impl",
    "struct",
    "trait",
    "pub",
    "async",
    "await",
})
_BUILTINS = frozenset(dir(builtins))


def _fence_language(info_string: str) -> str | None:
    token = info_string.strip().split()[0].lower() if info_string.strip() else ""
    return _FENCE_LANGUAGE.get(token)


def _is_historical_doc(rel_path: str) -> bool:
    lowered = rel_path.lower()
    return any(marker in lowered for marker in ("paper.md", "roadmap", "changelog", "history"))


def _iter_code_span_tokens(text: str) -> Iterator[tuple[str, int, str, str | None]]:
    """Yield (reference_text, line_number, span_kind, fence_language) for identifier tokens inside
    code spans. Prose is excluded. A line-state-machine tracks fenced blocks (and gives line
    numbers for free); the fence info-string line is metadata and is never scanned as code."""
    fence_char: str | None = None
    fence_lang: str | None = None
    for lineno, line in enumerate(text.splitlines(), start=1):
        fence_match = _FENCE_RE.match(line.strip())
        if fence_char is None:
            if fence_match:
                fence_char = fence_match.group(1)[0]
                fence_lang = _fence_language(fence_match.group(2))
                continue
            for span in _INLINE_RE.findall(line):
                for token in _TOKEN_RE.findall(span):
                    yield token, lineno, "inline-code", None
        else:
            if fence_match and fence_match.group(1)[0] == fence_char:
                fence_char = None
                fence_lang = None
                continue
            for token in _TOKEN_RE.findall(line):
                yield token, lineno, "fenced-code", fence_lang


def _symbol_of(reference_text: str) -> str:
    # Resolve the rightmost segment of a dotted path (obj.method -> method) as the referenced name.
    return reference_text.rsplit(".", 1)[-1]


def _is_skippable(symbol: str, reference_text: str) -> bool:
    if len(symbol) < _MIN_TOKEN_LEN:
        return True
    if reference_text.startswith("-"):
        return True
    if symbol in _LANGUAGE_KEYWORDS or symbol in _BUILTINS or symbol in _CURATED_STDLIB:
        return True
    dotted_or_qualified = "." in reference_text
    if symbol in _TG_COMMAND_NAMES and not dotted_or_qualified:
        return True
    if (
        symbol.lower() in _COMMON_WORD_STOPLIST
        and symbol.islower()
        and len(symbol) < 6
        and not dotted_or_qualified
    ):
        return True
    return False


def _confidence(span_kind: str, reference_text: str, symbol: str) -> str:
    qualified = "." in reference_text or symbol[:1].isupper() or "_" in symbol
    if span_kind == "fenced-code" and qualified and len(symbol) >= 6:
        return "high"
    return "low"


def build_doc_drift(
    docs_path: str = ".",
    *,
    code_path: str | None = None,
    max_files: int = DEFAULT_MAX_DIFF_DOCS_FILES,
) -> dict[str, Any]:
    """Scan docs under ``docs_path`` for code-span references to symbols that no longer resolve in
    the code under ``code_path`` (defaults to ``docs_path``). Raises ``FileNotFoundError`` when
    ``docs_path`` does not exist (fail closed)."""
    docs_root = Path(docs_path)
    if not docs_root.exists():
        raise FileNotFoundError(f"diff-docs path does not exist: {docs_path}")
    code_root = code_path if code_path is not None else docs_path

    # Build the symbol table ONCE (build_repo_map is the expensive AST scan); a set of names is the
    # exact "does this resolve?" check build_symbol_defs_from_map performs, at O(1) per token.
    repo_map = build_repo_map(code_root, max_repo_files=max_files)
    known_symbols = {str(sym["name"]) for sym in repo_map.get("symbols", [])}

    doc_files = [
        path for path in _iter_repo_files(docs_root, max_files=None) if path.suffix in _DOC_SUFFIXES
    ]
    truncated = len(doc_files) > max_files
    if truncated:
        doc_files = doc_files[:max_files]

    findings: list[dict[str, Any]] = []
    out_of_scope_files = 0
    seen_out_of_scope: set[str] = set()

    for path in doc_files:
        rel = _rel(path, docs_root)
        historical = _is_historical_doc(rel)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for reference_text, lineno, span_kind, fence_lang in _iter_code_span_tokens(text):
            # Scope-gate fenced spans by language BEFORE resolution (the python-default trap).
            if span_kind == "fenced-code":
                if fence_lang is None:
                    if rel not in seen_out_of_scope:
                        seen_out_of_scope.add(rel)
                        out_of_scope_files += 1
                    continue
                if fence_lang not in _IN_SCOPE_LANGUAGES:
                    continue
            symbol = _symbol_of(reference_text)
            if _is_skippable(symbol, reference_text):
                continue
            if symbol in known_symbols:
                continue  # resolves — not drift
            confidence = _confidence(span_kind, reference_text, symbol)
            if historical:
                confidence = "low"
            findings.append({
                "kind": "unresolved-symbol-reference",
                "doc_file": rel,
                "doc_line": lineno,
                "reference_text": reference_text,
                "span_kind": span_kind,
                "fence_language": fence_lang,
                "confidence": confidence,
                "reason": f"no definition found for {symbol!r} in the scanned code symbols",
            })

    findings.sort(key=lambda f: (f["doc_file"], f["doc_line"], f["reference_text"]))
    return {
        "version": DIFF_DOCS_SCHEMA_VERSION,
        "schema_version": DIFF_DOCS_SCHEMA_VERSION,
        "docs_path": str(docs_path),
        "code_path": str(code_root),
        "findings": findings,
        "coverage": {
            "language_scope": ["python", "javascript", "typescript", "rust"],
            "span_scope": "code-spans-only",
            "not_covered": [
                "signature-drift (symbol exists but args/types changed)",
                "semantic/behavior drift",
                "example execution / runnable-doctest verification",
                "prose mentions (identifiers outside code spans)",
                "out-of-scope-language docs (Go/Java/C/etc. get no symbol coverage)",
                "renamed-symbol / fuzzy detection",
            ],
            "docs_files_scanned": len(doc_files),
            "docs_files_out_of_scope": out_of_scope_files,
        },
        "scan_limit": {
            "max_files": max_files,
            "scanned_files": len(doc_files),
            "possibly_truncated": truncated,
            "truncation_cause": "project-files" if truncated else None,
        },
    }


def _rel(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def render_doc_drift_text(payload: dict[str, Any]) -> str:
    findings = payload["findings"]
    high = sum(1 for f in findings if f["confidence"] == "high")
    lines = [
        f"diff-docs: {len(findings)} unresolved reference(s) ({high} high-confidence) "
        f"across {payload['coverage']['docs_files_scanned']} doc file(s) ({payload['docs_path']})",
    ]
    oos = payload["coverage"]["docs_files_out_of_scope"]
    if oos:
        lines.append(f"  [!] {oos} doc file(s) reference out-of-scope languages - NOT verified.")
    for f in findings[:40]:
        lines.append(
            f"  {f['doc_file']}:{f['doc_line']}  {f['reference_text']}  "
            f"[{f['confidence']}/{f['span_kind']}]"
        )
    if len(findings) > 40:
        lines.append(f"  … and {len(findings) - 40} more")
    return "\n".join(lines)
