---
name: tensor-grep-add-language
description: Add or extend a language extractor and register its symbol, import, reference, and caller capabilities.
---

# Add language support

The symbol graph uses `LanguageSpec` in
`src/tensor_grep/cli/lang_registry.py`. Registrations live in
`src/tensor_grep/cli/repo_map.py`; language-specific modules include
`lang_go.py`, `lang_php.py`, and `lang_csharp.py`.

## Implementation checklist

1. Inspect the target parser's actual syntax tree with representative fixtures.
   Distinguish declarations, calls, imports, and ambiguous syntax.
2. Implement supported extractor operations in a language-specific module.
   Match the registry's current callable signatures and optional capabilities.
3. Register suffixes, grammar dependencies, parser availability, extraction
   functions, and provenance through `LanguageSpec`.
4. Inspect both suffix modules, `src/tensor_grep/cli/lang_suffixes.py` and
   `src/tensor_grep/core/lang_suffixes.py`, plus command language filters.
   Add only capabilities the new implementation actually supports.
5. Add optional grammar dependencies to `pyproject.toml` and regenerate
   `uv.lock`. Keep installations without the grammar usable.
6. Test definitions, source spans, imports, references, and callers where
   implemented. Include comments, strings, malformed syntax, missing grammars,
   and same-named symbols in unrelated files.

Do not infer a complete caller graph from definition extraction alone.
Report unsupported or unresolved cases through the existing gap/provenance
contracts. Preserve original source line numbers and avoid inventing dependency
edges.

Use `tests/unit/test_lang_registry.py` and existing language-specific tests
as entry points. Run affected tests with parsers installed and exercise the
missing-parser path separately. Update user documentation with the precise
supported capabilities.
