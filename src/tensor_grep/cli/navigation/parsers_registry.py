from __future__ import annotations

from typing import Any

from tensor_grep.cli import repo_map as _repo_map


def _js_ts_classify_ref_kind(node: Any) -> str:
    """Classify an already-matched JS/TS identifier/property_identifier reference (T1).

    Only called for nodes the existing walker already emits a row for -- ``type_identifier``
    (the node type TS uses for most type-position symbols, e.g. ``x: Symbol``) is never matched
    by the walker today, so widening to it is T2 (would add new rows, not just labels).
    """
    parent = getattr(node, "parent", None)
    # NOTE: tree-sitter's Python binding hands back a NEW wrapper object on every
    # child_by_field_name()/`.parent` access, so `is` identity comparison silently never matches
    # -- `==` (and `.id`) compare the underlying node and are what must be used here.
    if node.type == "identifier" and parent is not None and parent.type == "call_expression":
        function_node = parent.child_by_field_name("function")
        if function_node is not None and function_node == node:
            return "call"
    if node.type == "identifier" and parent is not None and parent.type == "new_expression":
        constructor_node = parent.child_by_field_name("constructor")
        if constructor_node is not None and constructor_node == node:
            return "call"
    if (
        node.type == "identifier"
        and parent is not None
        and parent.type in _repo_map._JS_TS_CONSTRUCTOR_CALL_ANCESTOR_TYPES
    ):
        name_node = parent.child_by_field_name("name")
        if name_node is not None and name_node == node:
            return "call"
    if (
        node.type == "property_identifier"
        and parent is not None
        and parent.type == "member_expression"
    ):
        grandparent = getattr(parent, "parent", None)
        if grandparent is not None and grandparent.type == "call_expression":
            function_node = grandparent.child_by_field_name("function")
            if function_node is not None and function_node == parent:
                return "call"
        return "field"
    if parent is not None and parent.type in _repo_map._JS_TS_AS_SATISFIES_TYPES:
        # Neither `as_expression` nor `satisfies_expression` exposes named grammar fields for its
        # two children, so the type side is identified positionally: it is always the LAST named
        # child (`operand as/satisfies TYPE`). Only that child is "type"; the operand falls
        # through to keep whatever kind it would have received anyway (call/field/value).
        type_side = (
            parent.named_child(parent.named_child_count - 1) if parent.named_child_count else None
        )
        if type_side is not None and type_side == node:
            return "type"
    if _repo_map._node_has_ancestor_type(node, _repo_map._JS_TS_TYPE_ANCESTOR_TYPES):
        return "type"
    return "value"


def _rust_classify_ref_kind(node: Any) -> str:
    """Classify an already-matched Rust ``identifier`` reference node (T1 additive).

    Only called for nodes the existing walker already emits a row for -- Rust's grammar splits
    type positions into ``type_identifier`` and field access into ``field_identifier`` (both
    distinct from plain ``identifier``), so those positions are never matched by the walker
    today; widening to them is T2 (would add new rows, not just labels).

    F6 fix: only the macro NAME position (e.g. ``println`` in ``println!(...)``) is "call" --
    ``macro_invocation``'s ``arguments`` token-tree can contain arbitrary identifiers
    (``println!("{:?}", Symbol)``, ``vec![Symbol]``) that are plain data/argument mentions, not
    calls, and must fall through to "value" like any other bare identifier. Checking ONLY the
    immediate parent (rather than any ancestor) is what keeps macro *arguments* out of this branch.

    F18 fix: a turbofish call (``foo::<T>()``) puts the function identifier under a
    ``generic_function`` node (itself the ``call_expression``'s ``function`` field), one layer
    deeper than a plain call -- both the direct-identifier and ``scoped_identifier`` (path-
    qualified) forms are handled below so ``foo::<T>()`` and ``bar::baz::<T>()`` both classify
    "call" instead of falling through to "value".
    """
    parent = getattr(node, "parent", None)
    # NOTE: see _js_ts_classify_ref_kind -- tree-sitter node accessors return fresh wrapper
    # objects, so identity must be checked with `==`, not `is`.
    if parent is not None and parent.type == "call_expression":
        function_node = parent.child_by_field_name("function")
        if function_node is not None and function_node == node:
            return "call"
    if parent is not None and parent.type == "generic_function":
        grandparent = getattr(parent, "parent", None)
        if grandparent is not None and grandparent.type == "call_expression":
            call_function_node = grandparent.child_by_field_name("function")
            if call_function_node is not None and call_function_node == parent:
                generic_function_name = parent.child_by_field_name("function")
                if generic_function_name is not None and generic_function_name == node:
                    return "call"
    if parent is not None and parent.type == "scoped_identifier":
        grandparent = getattr(parent, "parent", None)
        if grandparent is not None and grandparent.type == "call_expression":
            function_node = grandparent.child_by_field_name("function")
            if function_node is not None and function_node == parent:
                return "call"
        if grandparent is not None and grandparent.type == "generic_function":
            great_grandparent = getattr(grandparent, "parent", None)
            if great_grandparent is not None and great_grandparent.type == "call_expression":
                call_function_node = great_grandparent.child_by_field_name("function")
                if call_function_node is not None and call_function_node == grandparent:
                    generic_function_name = grandparent.child_by_field_name("function")
                    if generic_function_name is not None and generic_function_name == parent:
                        return "call"
    if parent is not None and parent.type == "macro_invocation":
        macro_name_node = parent.child_by_field_name("macro")
        if macro_name_node is not None and macro_name_node == node:
            return "call"
    return "value"


def _extract_braced_block(lines: list[str], start_index: int) -> tuple[int, str]:
    start_line = lines[start_index]
    start_line_num = start_index + 1
    brace_balance = start_line.count("{") - start_line.count("}")
    if brace_balance <= 0:
        return start_line_num, f"{start_line}\n"

    block_lines = [start_line]
    end_index = start_index
    for current_index in range(start_index + 1, len(lines)):
        current_line = lines[current_index]
        block_lines.append(current_line)
        brace_balance += current_line.count("{") - current_line.count("}")
        end_index = current_index
        if brace_balance <= 0:
            break
    return end_index + 1, "\n".join(block_lines) + "\n"


def _javascript_imports_and_symbols(path: _repo_map.Path) -> tuple[list[str], list[dict[str, Any]]]:
    current_imports, regex_symbols = _repo_map._regex_imports_and_symbols(path)
    current_symbols = _repo_map._dedupe_symbol_records([
        *_repo_map._js_ts_parser_symbols(path),
        *regex_symbols,
    ])
    return current_imports, current_symbols


def _typescript_imports_and_symbols(path: _repo_map.Path) -> tuple[list[str], list[dict[str, Any]]]:
    current_imports, regex_symbols = _repo_map._regex_imports_and_symbols(path)
    current_symbols = _repo_map._dedupe_symbol_records([
        *_repo_map._js_ts_parser_symbols(path),
        *regex_symbols,
    ])
    return current_imports, current_symbols


def _rust_imports_and_symbols(path: _repo_map.Path) -> tuple[list[str], list[dict[str, Any]]]:
    current_imports, _ = _repo_map._regex_imports_and_symbols(path)
    current_symbols = _repo_map._rust_parser_symbols(path)
    if not current_symbols:
        _, current_symbols = _repo_map._regex_imports_and_symbols(path)
    return current_imports, current_symbols


def _go_references_and_calls_for_registry(
    path: _repo_map.Path,
    symbol: str,
    repo_root: _repo_map.Path | str | None = None,
    *,
    definition_dirs: frozenset[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    # F25: forward definition_dirs so a package-qualified Go call only earns high-confidence
    # "go-import-resolution" when the resolved package actually owns this symbol.
    return _repo_map.lang_go.go_references_and_calls(
        path, symbol, repo_root, definition_dirs=definition_dirs
    )


def _csharp_references_and_calls_for_registry(
    path: _repo_map.Path,
    symbol: str,
    repo_root: _repo_map.Path | str | None = None,
    *,
    definition_dirs: frozenset[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    # F7 Task 11 wave 2: forward definition_dirs so a C# receiver whose namespace/`using`-
    # resolved FQN directory-suffix-matches the selected definition's directory earns the
    # cross-file confirmed band (mirrors Java's Task 11A adapter). lang_csharp.py already owns
    # its whole extraction pipeline including `_csharp_parser()` (matching lang_go.py/
    # lang_php.py's shape), so this adapter forwards path/symbol/repo_root/definition_dirs.
    return _repo_map.lang_csharp.csharp_references_and_calls(
        path, symbol, repo_root, definition_dirs=definition_dirs
    )


def _php_references_and_calls_for_registry(
    path: _repo_map.Path,
    symbol: str,
    repo_root: _repo_map.Path | str | None = None,
    *,
    definition_dirs: frozenset[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    # F7 Task 11 wave 2b: forward definition_dirs so a PHP receiver/scope whose `use`/namespace-
    # resolved FQN directory-suffix-matches the selected definition's directory earns the
    # cross-file confirmed band (mirrors Java's Task 11A adapter above). lang_php.py already owns
    # its whole extraction pipeline including `_php_parser()` (matching lang_go.py's/
    # lang_csharp.py's shape -- PHP's parser factory predates Task 10C, built for
    # php_imports_and_symbols), so this adapter forwards path/symbol/repo_root/definition_dirs.
    return _repo_map.lang_php.php_references_and_calls(
        path, symbol, repo_root, definition_dirs=definition_dirs
    )


def _c_references_and_calls_for_registry(
    path: _repo_map.Path,
    symbol: str,
    repo_root: _repo_map.Path | str | None = None,
    *,
    definition_dirs: frozenset[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    # Task 11 / F7 wave 3: forward repo_root + definition_dirs so a bare C call whose file
    # `#include`-resolves into the selected definition's directory earns the include-path
    # confirmed band (mirrors Java Task 11A / Go F25).
    return _repo_map.lang_c.c_references_and_calls(
        path, symbol, repo_root, definition_dirs=definition_dirs
    )


def _cpp_references_and_calls_for_registry(
    path: _repo_map.Path,
    symbol: str,
    repo_root: _repo_map.Path | str | None = None,
    *,
    definition_dirs: frozenset[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    # Task 11 / F7 wave 3: forward repo_root + definition_dirs so a bare/qualified C++ call
    # whose file `#include`-resolves into the selected definition's directory earns the
    # include-path confirmed band (mirrors C / Java Task 11A / Go F25).
    return _repo_map.lang_cpp.cpp_references_and_calls(
        path, symbol, repo_root, definition_dirs=definition_dirs
    )


def _references_and_calls_for_path(
    path: _repo_map.Path,
    symbol: str,
    repo_root: _repo_map.Path | str | None = None,
    *,
    definition_dirs: frozenset[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Resolve references and calls for *path* through the language registry.

    Looks up ``lang_registry.spec_for_path(path)`` and invokes
    ``spec.references_and_calls`` when that field is set. Every registered extractor is
    reached through a thin adapter with this SAME signature (``definition_dirs`` forwarded
    for Go F25, ignored elsewhere) so this seam never branches on ``language_id``.

    EXPLICIT fallback: when the path has no registered extractor (``spec is None``, or an
    unregistered suffix), call ``_regex_references_and_calls``. As of Task 10E (C++, the final
    wave of the top-10 language-support campaign) every REGISTERED language carries a real
    ``references_and_calls`` extractor -- ``spec.references_and_calls is None`` can no longer
    happen for any language in ``LANGUAGE_REGISTRY`` (see
    ``test_every_registered_language_is_parser_backed_after_the_final_wave``), so this branch is
    now reached only by a path whose suffix has no registered ``LanguageSpec`` at all. Java/C#/
    PHP/C/C++ shipped in-file ``references_and_calls`` extractors (Tasks 10A/10B/10C/10D/10E);
    their cross-file caller confirmation still uses the same text-prefilter mechanism as every
    other language, just reached through a different seam. That fallback is deliberate and
    documented, never an implicit empty result.

    Secondary degrade paths that used to live inside the per-language_id if/elif chains
    (provider-alias then regex for JS/TS/Rust; fail-closed empty for Go/python) stay at the
    call sites, gated on registry fields (``provider_alias_calls``,
    ``provenance_when_missing``) rather than ``language_id`` -- see
    ``build_symbol_refs_from_map`` / ``build_symbol_callers_from_map``.
    """
    spec = _repo_map.lang_registry.spec_for_path(path)
    if spec is not None and spec.references_and_calls is not None:
        # Uniform adapter call: wrappers accept definition_dirs (see registrations below).
        return spec.references_and_calls(
            path,
            symbol,
            repo_root,
            definition_dirs=definition_dirs,  # type: ignore[call-arg]
        )
    # Explicit documented fallback for references_and_calls is None / unregistered path.
    return _repo_map._regex_references_and_calls(path, symbol)


def _prime_all_language_repo_contexts(context_root: _repo_map.Path) -> None:
    """Prime every registered language's per-repo-root context exactly once.

    Registry-driven replacement for the previous hardcoded
    ``_prime_js_ts_repo_context(...); _prime_rust_repo_context(...)`` pair. javascript and
    typescript are separate LanguageSpec entries that both point at the SAME
    ``_prime_js_ts_repo_context`` callable -- dedupe by callable identity so a repo with both
    suffixes present still primes the shared JS/TS context exactly once, matching prior
    behavior (which called it a single time regardless of how many JS/TS suffixes appeared).
    """
    primed: set[int] = set()
    for spec in _repo_map.lang_registry.LANGUAGE_REGISTRY.values():
        if spec.prime_repo_context is None:
            continue
        if id(spec.prime_repo_context) in primed:
            continue
        primed.add(id(spec.prime_repo_context))
        spec.prime_repo_context(context_root)


def _imports_and_symbols_for_path(
    path: _repo_map.Path,
    *,
    _profiling_collector: _repo_map._ProfileCollector | None = None,
) -> tuple[list[str], list[dict[str, Any]]]:
    # O2: skip files that exceed the per-file byte cap to bound RSS spikes from
    # multi-MB bundled/generated files.  The cap is configurable via the
    # TENSOR_GREP_MAX_PARSE_BYTES env var (default 2 MB).
    try:
        file_size = _repo_map._file_size_with_receipt(path)
    except OSError:
        file_size = 0
    if file_size > _repo_map._max_parse_bytes():
        return [], []
    with _repo_map._profiling_phase(_profiling_collector, "file_parse"):
        spec = _repo_map.lang_registry.spec_for_path(path)
        if spec is not None and spec.extract_imports_and_symbols is not None:
            current_imports, current_symbols = spec.extract_imports_and_symbols(path)
        else:
            current_imports, current_symbols = _repo_map._python_imports_and_symbols(path)
            if not current_imports and not current_symbols:
                current_imports, current_symbols = _repo_map._regex_imports_and_symbols(path)
        return current_imports, current_symbols


def _imports_with_lines_for_path(path: _repo_map.Path) -> list[dict[str, Any]]:
    """Raw per-statement imports with 1-based line numbers for the 10 supported languages.

    Returns ``[]`` for an unsupported language (e.g. Kotlin) or an over-cap file -- callers that
    need to distinguish "genuinely no imports" from "not scanned" must check those conditions
    themselves (see ``build_file_imports``'s ``result_incomplete`` handling).
    """
    spec = _repo_map.lang_registry.spec_for_path(path)
    if spec is None:
        return []
    if spec.language_id == "python":
        return _repo_map._python_imports_with_lines(path)
    if spec.language_id in ("javascript", "typescript"):
        return _repo_map._js_ts_imports_with_lines(path)
    if spec.language_id == "rust":
        return _repo_map._rust_imports_with_lines(path)
    if spec.language_id == "java":
        return _repo_map._java_imports_with_lines(path)
    if spec.language_id in ("go", "php", "csharp", "c", "cpp"):
        # go/php/csharp/c/cpp's own extractors (lang_go.py/lang_php.py/lang_csharp.py/lang_c.py/
        # lang_cpp.py) mirror their `_X_imports_and_symbols` siblings, which get this SAME cap
        # check for free from THEIR caller (`_imports_and_symbols_for_path`, above) rather than
        # self-guarding -- applied here once, at this dispatcher, for the identical reason.
        try:
            file_size = _repo_map._file_size_with_receipt(path)
        except OSError:
            file_size = 0
        if file_size > _repo_map._max_parse_bytes():
            return []
        if spec.language_id == "go":
            return _repo_map.lang_go.go_imports_with_lines(path)
        if spec.language_id == "php":
            return _repo_map.lang_php.php_imports_with_lines(path)
        if spec.language_id == "csharp":
            return _repo_map.lang_csharp.csharp_imports_with_lines(path)
        if spec.language_id == "c":
            return _repo_map.lang_c.c_imports_with_lines(path)
        return _repo_map.lang_cpp.cpp_imports_with_lines(path)
    return []


def _group_symbols_by_file(symbols: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for symbol in symbols:
        current_path = str(symbol["file"])
        current_symbols = grouped.setdefault(current_path, [])
        current_symbols.append(dict(symbol))
    for current_symbols in grouped.values():
        current_symbols.sort(
            key=lambda item: (item["file"], item["line"], item["kind"], item["name"])
        )
    return grouped


def _normalized_changeset_paths(
    root: _repo_map.Path,
    changeset: dict[str, Any],
) -> dict[str, list[str]]:
    normalized: dict[str, list[str]] = {}
    for key in ("added", "modified", "removed"):
        entries: list[str] = []
        for raw_path in changeset.get(key, []) or []:
            current_path = _repo_map.Path(str(raw_path)).expanduser()
            if not current_path.is_absolute():
                current_path = root / current_path
            entries.append(str(_repo_map._resolved_input_path(current_path)))
        normalized[key] = sorted(dict.fromkeys(entries))
    return normalized


def _register_repo_map_languages() -> None:
    _repo_map.lang_registry.register_language(
        _repo_map.lang_registry.LanguageSpec(
            language_id="python",
            suffixes=frozenset({".py"}),
            grammar_modules=(),
            parser_for_path=None,
            provenance_when_parsed="python-ast",
            provenance_when_missing="python-ast",
            import_markers=(b"import ", b"from "),
            def_node_kinds=("ClassDef", "FunctionDef", "AsyncFunctionDef"),
            extract_imports_and_symbols=_repo_map._python_imports_and_symbols,
            references_and_calls=_repo_map._python_references_and_calls_for_registry,
            provider_alias_calls=_repo_map._python_provider_alias_calls_for_registry,
            file_imports_symbol_from_definition=_repo_map._python_file_imports_symbol_from_definition,
            import_update_target=_repo_map._python_import_update_target_for_registry,
            prime_repo_context=None,
            classify_ref_kind=_repo_map._python_classify_ref_kind,
        )
    )
    _JS_TS_REGISTRY_SHARED_KWARGS: dict[str, Any] = {
        "grammar_modules": ("tree_sitter", "tree_sitter_javascript", "tree_sitter_typescript"),
        "provenance_when_parsed": "tree-sitter",
        "provenance_when_missing": "regex-heuristic",
        "import_markers": (b"import ", b"from ", b"require("),
        "def_node_kinds": ("function_declaration", "class_declaration", "method_definition"),
        "references_and_calls": _repo_map._js_ts_references_and_calls_for_registry,
        "provider_alias_calls": _repo_map._js_ts_provider_alias_calls,
        "file_imports_symbol_from_definition": _repo_map._js_ts_file_imports_symbol_from_definition,
        "import_update_target": _repo_map._js_ts_import_update_target,
        "prime_repo_context": _repo_map._prime_js_ts_repo_context,
        "classify_ref_kind": _repo_map._js_ts_classify_ref_kind,
    }
    _repo_map.lang_registry.register_language(
        _repo_map.lang_registry.LanguageSpec(
            language_id="javascript",
            suffixes=frozenset({".js", ".jsx", ".mjs", ".cjs"}),
            parser_for_path=lambda path: _repo_map._javascript_parser(),
            extract_imports_and_symbols=_repo_map._javascript_imports_and_symbols,
            **_JS_TS_REGISTRY_SHARED_KWARGS,
        )
    )
    _repo_map.lang_registry.register_language(
        _repo_map.lang_registry.LanguageSpec(
            language_id="typescript",
            suffixes=frozenset(_repo_map.lang_suffixes.TS_SUFFIXES),
            parser_for_path=lambda path: _repo_map._typescript_parser(
                tsx=path.suffix.lower() == ".tsx"
            ),
            extract_imports_and_symbols=_repo_map._typescript_imports_and_symbols,
            **_JS_TS_REGISTRY_SHARED_KWARGS,
        )
    )
    _repo_map.lang_registry.register_language(
        _repo_map.lang_registry.LanguageSpec(
            language_id="rust",
            suffixes=frozenset({".rs"}),
            grammar_modules=("tree_sitter", "tree_sitter_rust"),
            parser_for_path=lambda path: _repo_map._rust_parser(),
            provenance_when_parsed="tree-sitter",
            provenance_when_missing="regex-heuristic",
            import_markers=(b"use ",),
            def_node_kinds=("function_item", "struct_item", "enum_item", "trait_item"),
            extract_imports_and_symbols=_repo_map._rust_imports_and_symbols,
            references_and_calls=_repo_map._rust_references_and_calls_for_registry,
            provider_alias_calls=_repo_map._rust_provider_alias_calls,
            file_imports_symbol_from_definition=_repo_map._rust_file_imports_symbol_from_definition,
            import_update_target=_repo_map._rust_import_update_target,
            prime_repo_context=_repo_map._prime_rust_repo_context,
            classify_ref_kind=_repo_map._rust_classify_ref_kind,
        )
    )
    _repo_map.lang_registry.register_language(
        _repo_map.lang_registry.LanguageSpec(
            language_id="go",
            suffixes=frozenset({".go"}),
            grammar_modules=("tree_sitter", "tree_sitter_go"),
            parser_for_path=lambda path: _repo_map.lang_go._go_parser(),
            provenance_when_parsed="tree-sitter",
            provenance_when_missing="grammar-missing",
            import_markers=(b"import ",),
            def_node_kinds=(
                "function_declaration",
                "method_declaration",
                "type_spec",
                "const_spec",
                "var_spec",
            ),
            extract_imports_and_symbols=_repo_map.lang_go.go_imports_and_symbols,
            references_and_calls=_repo_map._go_references_and_calls_for_registry,
            provider_alias_calls=None,
            file_imports_symbol_from_definition=_repo_map.lang_go.go_file_imports_symbol_from_definition,
            import_update_target=None,
            prime_repo_context=_repo_map.lang_go.prime_go_repo_context,
            classify_ref_kind=None,
        )
    )
    _repo_map.lang_registry.register_language(
        _repo_map.lang_registry.LanguageSpec(
            language_id="java",
            suffixes=frozenset({".java"}),
            grammar_modules=("tree_sitter", "tree_sitter_java"),
            parser_for_path=lambda path: _repo_map._java_parser(),
            provenance_when_parsed="tree-sitter",
            provenance_when_missing="grammar-missing",
            import_markers=(b"import ",),
            def_node_kinds=(
                "class_declaration",
                "interface_declaration",
                "enum_declaration",
                "record_declaration",
                "method_declaration",
                "constructor_declaration",
            ),
            extract_imports_and_symbols=_repo_map._java_imports_and_symbols,
            references_and_calls=_repo_map._java_references_and_calls_for_registry,
            provider_alias_calls=None,
            file_imports_symbol_from_definition=_repo_map.lang_java.java_file_imports_symbol_from_definition,
            import_update_target=None,
            prime_repo_context=None,
            classify_ref_kind=None,
        )
    )
    _repo_map.lang_registry.register_language(
        _repo_map.lang_registry.LanguageSpec(
            language_id="php",
            suffixes=frozenset({".php"}),
            grammar_modules=("tree_sitter", "tree_sitter_php"),
            parser_for_path=lambda path: _repo_map.lang_php._php_parser(),
            provenance_when_parsed="tree-sitter",
            provenance_when_missing="grammar-missing",
            import_markers=(b"use ",),
            def_node_kinds=(
                "class_declaration",
                "interface_declaration",
                "trait_declaration",
                "enum_declaration",
                "function_definition",
                "method_declaration",
            ),
            extract_imports_and_symbols=_repo_map.lang_php.php_imports_and_symbols,
            references_and_calls=_repo_map._php_references_and_calls_for_registry,
            provider_alias_calls=None,
            file_imports_symbol_from_definition=_repo_map.lang_php.php_file_imports_symbol_from_definition,
            import_update_target=None,
            prime_repo_context=None,
            classify_ref_kind=None,
        )
    )
    _repo_map.lang_registry.register_language(
        _repo_map.lang_registry.LanguageSpec(
            language_id="csharp",
            suffixes=frozenset({".cs"}),
            grammar_modules=("tree_sitter", "tree_sitter_c_sharp"),
            parser_for_path=lambda path: _repo_map.lang_csharp._csharp_parser(),
            provenance_when_parsed="tree-sitter",
            provenance_when_missing="grammar-missing",
            import_markers=(b"using ",),
            def_node_kinds=(
                "class_declaration",
                "interface_declaration",
                "struct_declaration",
                "enum_declaration",
                "record_declaration",
                "method_declaration",
                "constructor_declaration",
            ),
            extract_imports_and_symbols=_repo_map.lang_csharp.csharp_imports_and_symbols,
            references_and_calls=_repo_map._csharp_references_and_calls_for_registry,
            provider_alias_calls=None,
            file_imports_symbol_from_definition=_repo_map.lang_csharp.csharp_file_imports_symbol_from_definition,
            import_update_target=None,
            prime_repo_context=None,
            classify_ref_kind=None,
        )
    )
    _repo_map.lang_registry.register_language(
        _repo_map.lang_registry.LanguageSpec(
            language_id="c",
            suffixes=frozenset({".c"}),
            grammar_modules=("tree_sitter", "tree_sitter_c"),
            parser_for_path=lambda path: _repo_map.lang_c._c_parser(),
            provenance_when_parsed="tree-sitter",
            provenance_when_missing="grammar-missing",
            import_markers=(b"#include",),
            def_node_kinds=(
                "function_definition",
                "declaration",
                "struct_specifier",
                "union_specifier",
                "enum_specifier",
                "type_definition",
            ),
            extract_imports_and_symbols=_repo_map.lang_c.c_imports_and_symbols,
            references_and_calls=_repo_map._c_references_and_calls_for_registry,
            provider_alias_calls=None,
            file_imports_symbol_from_definition=_repo_map.lang_c.c_file_imports_symbol_from_definition,
            import_update_target=None,
            prime_repo_context=None,
            classify_ref_kind=None,
        )
    )
    _repo_map.lang_registry.register_language(
        _repo_map.lang_registry.LanguageSpec(
            language_id="cpp",
            suffixes=frozenset(_repo_map._CPP_SUFFIXES),
            grammar_modules=("tree_sitter", "tree_sitter_cpp"),
            parser_for_path=lambda path: _repo_map.lang_cpp._cpp_parser(),
            provenance_when_parsed="tree-sitter",
            provenance_when_missing="grammar-missing",
            import_markers=(b"#include",),
            def_node_kinds=(
                "function_definition",
                "declaration",
                "field_declaration",
                "class_specifier",
                "struct_specifier",
                "union_specifier",
                "enum_specifier",
                "namespace_definition",
                "template_declaration",
                "type_definition",
                "alias_declaration",
            ),
            extract_imports_and_symbols=_repo_map.lang_cpp.cpp_imports_and_symbols,
            references_and_calls=_repo_map._cpp_references_and_calls_for_registry,
            provider_alias_calls=None,
            file_imports_symbol_from_definition=_repo_map.lang_cpp.cpp_file_imports_symbol_from_definition,
            import_update_target=None,
            prime_repo_context=None,
            classify_ref_kind=None,
        )
    )
