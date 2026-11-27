from __future__ import annotations

import re

from tensor_grep.cli import lang_suffixes

CALLER_SCAN_FILE_CEILING = 2000
_JS_TS_SUFFIXES = set(lang_suffixes.JS_TS_SUFFIXES)
_RUST_SUFFIXES = {".rs"}
_JAVA_SUFFIXES = {".java"}


_SKIP_DIR_NAMES = {
    ".tensor-grep",
    ".tg_cache",
    # tg-owned index/reference trees â€” never product source, and can be large enough to
    # hang an unscoped walk (critical unscoped-search-hang audit). Kept in sync with
    # docs_coverage.py's _EXCLUDED_DIR_PARTS, which already excludes these.
    "_tg_refs",
    ".tg_semantic_index",
    "external_repos",
    ".git",
    ".hg",
    ".svn",
    ".venv",
    ".mypy_cache",
    ".next",
    ".nyc_output",
    ".parcel-cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tmp",
    ".venv_cuda",
    ".tox",
    ".turbo",
    ".vite",
    ".vitest",
    "__pycache__",
    "artifacts",
    "bench_data",
    "build",
    "coverage",
    "dist",
    "gpu_bench_data",
    "group2_many_files",
    "htmlcov",
    "many_files",
    "node_modules",
    "out",
    "site",
    "target",
    "temp",
    "tmp",
    "tmp_agent_probe",
    "venv",
    ".cache",
    ".nox",
    # additional vendor/cache dirs that can slip through walk ordering
    "site-packages",
    "vendor",
    "pods",
    "gems",
    ".bundle",
    ".gradle",
    ".cargo",
    "bower_components",
}
_VENDOR_CACHE_DIR_COMPONENTS: frozenset[str] = frozenset(
    _SKIP_DIR_NAMES
    | {
        # extra names that appear as sub-directory components but may not be
        # top-level walk roots (so _should_skip_repo_dir never sees them directly).
        # NB: do NOT add bare "lib" here â€” it is a common SOURCE directory; the
        # vendored case is lib/.../site-packages, already covered by "site-packages".
        # Misclassifying lib/ project files as vendor makes possibly_truncated False,
        # which silently disables blast-radius literal-symbol seeding.
        "site_packages",  # older virtualenv layout
    }
)
_SOURCE_FIRST_DIR_NAMES = {
    ".claude",
    "app",
    "apps",
    "bin",
    "cmd",
    "crates",
    "lib",
    "packages",
    "rust_core",
    "scripts",
    "src",
    "tools",
}
_SOURCE_FIRST_SUFFIXES = {
    ".c",
    ".cc",
    ".cpp",
    ".cs",
    ".css",
    ".cxx",
    ".go",
    ".h",
    ".hh",
    ".hpp",
    ".hxx",
    ".java",
    ".kt",
    ".lua",
    ".php",
    ".py",
    ".rs",
    ".swift",
    # Scan-universe only (no extractor yet): the existing spec-less coverage gap fires for these.
    ".cu",
    ".cuh",
    ".csx",
    ".inl",
    ".ipp",
    ".phtml",
    ".tpp",
    *lang_suffixes.JS_TS_SUFFIXES,
}
_QUERY_TERM_SYNONYMS = {
    "resolved": ("resolve",),
    "resolving": ("resolve",),
    "resolution": ("resolve",),
}
_RUST_TEST_FN_PATTERN = re.compile(
    r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?fn\s+([A-Za-z_][A-Za-z0-9_]*)\b"
)
_SCAN_LIMIT_TRUNCATED_REMEDIATION = (
    "A truncated scan dropped project files, so a zero or small count is NOT trustworthy. "
    "Re-run scoped to a subdirectory PATH, raise --max-repo-files, or warm the index with "
    "`tg session daemon start`."
)
_CALLER_SCAN_CEILING_REMEDIATION = (
    f"The caller-scan was bounded to the first {CALLER_SCAN_FILE_CEILING} repo-map files for "
    "latency, even though the map covers more; callers/references in files past that window "
    "may be missing. Narrow PATH to a subdirectory containing the symbol for full coverage."
)
_DEADLINE_TRUNCATED_REMEDIATION = (
    "The scan stopped at the --deadline before finishing, so a zero or small count is NOT "
    "trustworthy. Re-run with a larger --deadline, scope to a subdirectory PATH, or warm the "
    "index with `tg session daemon start`."
)
_CLEAN_SYMBOL_NAME_RE = re.compile(r"^[^\s\x00-\x23\x25-\x2f\x3a-\x40\x5b-\x5e\x60\x7b-\x7f]+$")
_FALLBACK_SOURCE_SUFFIXES = {
    ".adoc",
    ".cfg",
    ".ini",
    ".json",
    ".md",
    ".markdown",
    ".rst",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}
_CODE_CONTEXT_SUFFIXES = _SOURCE_FIRST_SUFFIXES | _JS_TS_SUFFIXES | _RUST_SUFFIXES | {".py"}
_CONTEXT_FILE_SUFFIXES = _CODE_CONTEXT_SUFFIXES | _FALLBACK_SOURCE_SUFFIXES
_NON_CODE_HIDDEN_SUFFIXES = {
    ".bak",
    ".bin",
    ".cache",
    ".db",
    ".log",
    ".pid",
    ".sqlite",
    ".tmp",
}
_JAVA_SYMBOL_KIND_MAP: dict[str, str] = {
    "class_declaration": "class",
    "interface_declaration": "class",
    "enum_declaration": "class",
    "record_declaration": "class",
    "method_declaration": "function",
    "constructor_declaration": "function",
}
_JS_TS_TYPE_ANCESTOR_TYPES: set[str] = {
    "type_annotation",
    "extends_clause",
    "implements_clause",
    "generic_type",
}
_JS_TS_CONSTRUCTOR_CALL_ANCESTOR_TYPES: set[str] = {
    "jsx_self_closing_element",
    "jsx_opening_element",
    "jsx_closing_element",
}
_QUERY_LANGUAGE_ALIASES = {
    "python": "python",
    "py": "python",
    "typescript": "typescript",
    "ts": "typescript",
    "javascript": "javascript",
    "js": "javascript",
    "rust": "rust",
    "rs": "rust",
    # M13 audit: cover the remaining 6 registered languages so query_language_hints no longer
    # silently degrades to [] for them (fail-open: the capsule's mismatch-cap and candidate
    # filter never fire for go/java/php/csharp/c/cpp targets). A spurious hint errs toward the
    # conservative ask/filter, never a confident-wrong (the repo's honesty-over-speed posture).
    "go": "go",
    "golang": "go",
    "java": "java",
    "php": "php",
    "csharp": "csharp",
    "c_sharp": "csharp",
    "c": "c",
    "cpp": "cpp",
    "cplusplus": "cpp",
}
_LLM_CONTEXT_RENDER_OMITTED_KEYS = (
    "candidate_edit_targets",
    "coverage",
    "coverage_summary",
    "file_matches",
    "file_summaries",
    "graph_trust_summary",
    "edit_order",
    "plan",
    "primary_target",
    "test_matches",
)
_SUPPORTED_FILE_DEPENDENCY_LANGUAGES = frozenset({
    "python",
    "javascript",
    "typescript",
    "rust",
    "java",
    # #74-follow-up (2026-07-23 completeness audit): go/php/csharp join at the SAME
    # foundational tier java landed at -- raw import statements + line numbers via
    # lang_go.go_imports_with_lines / lang_php.php_imports_with_lines /
    # lang_csharp.csharp_imports_with_lines, `_resolve_raw_import_entry` reporting them honestly
    # unresolved (never a fabricated `resolved` path or a fabricated `external=True`). TRUE
    # import-string -> target-file resolution (and the `tg importers` reverse-edge CONFIRM step,
    # gated separately by `_confirm_import_edges`'s own language tuple below) stays deferred for
    # all three: parser-backed per-file facts do not provide cross-file target resolution.
    "go",
    "php",
    "csharp",
    # Top-10 language campaign (Phase 1, C; Phase 2, C++ -- closes the campaign to 10/10): raw
    # `#include` directives + line numbers via lang_c.c_imports_with_lines /
    # lang_cpp.cpp_imports_with_lines, `_resolve_raw_import_entry` reporting them honestly
    # unresolved. TRUE `#include` -> file resolution is deferred and harder than go/php/csharp's
    # own deferred resolvers -- C/C++ have no standardized manifest (no
    # go.mod/composer.json/.csproj equivalent) to resolve against.
    "c",
    "cpp",
})
