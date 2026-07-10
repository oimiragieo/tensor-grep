"""PERF increment 1 (Fable-designed): parse-product cache.

_js_ts_parser_symbols, _js_ts_references_and_calls, _rust_parser_symbols,
_rust_references_and_calls, and _js_ts_import_update_target each used to independently
`path.read_text(...)` + `parser.parse(...)` the SAME file -- caller_scan and edit-plan seeding
could re-parse one file up to 3x per symbol lookup (_js_ts_import_update_target alone profiled
at ~26% of edit_plan wall time). This suite exercises the golden-parity plan for the fix:

  1. one-parse spy: build_repo_map -> build_symbol_callers_from_map -> build_symbol_refs_from_map
     parses each JS/TS file at most once total; a second full pass adds zero more parses.
  2. staleness: editing a file to a DIFFERENT byte length invalidates the parse-product cache
     (the (mtime_ns, size) key changes) and forces a re-parse.
  3. early-exit: a file with neither the literal symbol nor any resolved import alias skips
     parsing entirely; a barrel consumer that only imports an ALIAS of the target symbol
     (`import { x as y } from "./barrel"`) still parses (alias-aware) and resolves correctly.
  4. gate: _file_may_import_symbol_definition's JS/TS branch is sound -- False for
     comment-only/export-only files with zero real import bindings, True for each import
     binding kind, and (the trap a naive definition-file-alias mirror would fail) True for a
     barrel consumer whose own import statement never mentions the definition file at all.
  5. daemon flush: _clear_all_source_caches() sweeps both the new text cache and the new
     parse-product cache.
  6. oversize bypass: a file over the byte cap bypasses the parse-product cache -- correct
     output every time, but never cached (no cache growth).
"""

from __future__ import annotations

from pathlib import Path

from tensor_grep.cli import repo_map


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _normalize(file_str: str) -> str:
    return str(file_str).replace("\\", "/")


def _spy_parse_calls(monkeypatch) -> dict[str, int]:
    """Patch the single choke point every parse-product cache miss funnels through, so the
    number of calls to it is exactly the number of REAL tree-sitter parses performed."""
    calls = {"n": 0}
    original = repo_map._parse_source_uncached

    def counting(path_str: str):
        calls["n"] += 1
        return original(path_str)

    monkeypatch.setattr(repo_map, "_parse_source_uncached", counting)
    return calls


# ---------------------------------------------------------------------------
# (1) one-parse spy
# ---------------------------------------------------------------------------


def test_map_then_callers_then_refs_parses_each_file_at_most_once(tmp_path, monkeypatch) -> None:
    root = tmp_path / "project"
    _write(
        root / "src" / "core.ts",
        "export function createInvoice(total) {\n  return total + 1;\n}\n",
    )
    _write(
        root / "src" / "caller_a.ts",
        'import { createInvoice } from "./core";\n\n'
        "export function useA(total) {\n"
        "  return createInvoice(total);\n"
        "}\n",
    )
    _write(
        root / "src" / "caller_b.ts",
        'import { createInvoice } from "./core";\n\n'
        "export function useB(total) {\n"
        "  return createInvoice(total);\n"
        "}\n",
    )
    _write(
        root / "src" / "unrelated.ts",
        "export function formatLabel(itemId) {\n  return `item-${itemId}`;\n}\n",
    )

    calls = _spy_parse_calls(monkeypatch)

    repo_map_payload = repo_map.build_repo_map(str(root))
    repo_map.build_symbol_callers_from_map(repo_map_payload, "createInvoice")
    repo_map.build_symbol_refs_from_map(repo_map_payload, "createInvoice")

    file_count = 4
    assert calls["n"] == file_count, (
        f"expected exactly {file_count} real parses (one per file), saw {calls['n']}"
    )

    # A second full pass over the SAME (unchanged) map must add zero additional parses.
    repo_map.build_symbol_callers_from_map(repo_map_payload, "createInvoice")
    repo_map.build_symbol_refs_from_map(repo_map_payload, "createInvoice")
    assert calls["n"] == file_count, "warm cache: a second pass must not re-parse any file"


def test_callers_then_refs_over_1024_files_stays_one_parse_per_file(tmp_path, monkeypatch) -> None:
    """backlog #57 companion-fix regression: _PARSE_PRODUCT_CACHE_MAXSIZE must stay >=
    CALLER_SCAN_FILE_CEILING (2000) or this FIFO (insertion-order, not LRU -- see
    _mtime_aware_cache) cache silently thrashes once a callers+refs pass touches more files than
    its capacity. At the OLD 1024 cap, files parsed early in the callers pass over a >1024-file
    universe get evicted before the refs pass (which revisits the SAME file universe) can reuse
    them, defeating the "one parse per file, shared across every symbol/ref/caller extractor"
    guarantee this cache exists to provide -- silently turning an O(N) scan into ~O(2N) at scale.
    Builds 1100 (> the old 1024 cap) TS files that ALL reference the target symbol, so every one
    of them is genuinely re-parsed by both the callers AND the refs scan, and proves the total
    real-parse count never exceeds one per file through the full pass."""
    root = tmp_path / "project"
    _write(
        root / "src" / "core.ts",
        "export function createInvoice(total) {\n  return total + 1;\n}\n",
    )
    file_count = 1100
    caller_count = file_count - 1
    for index in range(caller_count):
        _write(
            root / "src" / f"caller_{index:05d}.ts",
            'import { createInvoice } from "./core";\n\n'
            f"export function use_{index}(total) {{\n"
            "  return createInvoice(total);\n"
            "}\n",
        )

    calls = _spy_parse_calls(monkeypatch)

    repo_map_payload = repo_map.build_repo_map(str(root), max_repo_files=file_count + 100)
    assert len(repo_map_payload["files"]) == file_count

    repo_map.build_symbol_callers_from_map(repo_map_payload, "createInvoice")
    assert calls["n"] == file_count, (
        f"callers pass: expected exactly {file_count} real parses (one per file), saw {calls['n']}"
    )

    repo_map.build_symbol_refs_from_map(repo_map_payload, "createInvoice")
    assert calls["n"] == file_count, (
        "refs pass over the SAME >1024-file universe must be served entirely from the warm "
        f"parse-product cache (still {file_count} total real parses, one per file) -- a FIFO "
        f"cache smaller than the file count would force re-parses here; saw {calls['n']}"
    )


# ---------------------------------------------------------------------------
# (2) staleness
# ---------------------------------------------------------------------------


def test_edit_with_different_byte_length_forces_reparse(tmp_path, monkeypatch) -> None:
    core = _write(
        tmp_path / "core.ts",
        "export function createInvoice(total) {\n  return total + 1;\n}\n",
    )
    calls = _spy_parse_calls(monkeypatch)

    first = repo_map._parsed_source_and_tree(str(core))
    assert first is not None
    assert calls["n"] == 1

    # Same content, same (mtime_ns, size) key in practice -- calling again must hit the cache.
    second = repo_map._parsed_source_and_tree(str(core))
    assert second is not None
    assert calls["n"] == 1

    # Rewrite with a DIFFERENT byte length -- the (mtime_ns, size) key changes, forcing a
    # fresh parse with no explicit cache_clear() needed.
    core.write_text(
        "export function createInvoice(total) {\n  return total + 2;\n}\n\n// extra comment\n",
        encoding="utf-8",
    )
    third = repo_map._parsed_source_and_tree(str(core))
    assert third is not None
    assert calls["n"] == 2
    assert third[0] != first[0]


# ---------------------------------------------------------------------------
# (3) early-exit
# ---------------------------------------------------------------------------


def test_early_exit_symbol_absent_skips_parse_entirely(tmp_path, monkeypatch) -> None:
    target = _write(
        tmp_path / "leaf.ts",
        "export function unrelatedThing() {\n  return 1;\n}\n",
    )
    calls = _spy_parse_calls(monkeypatch)

    references, matched_calls = repo_map._js_ts_references_and_calls(target, "createInvoice")

    assert references == []
    assert matched_calls == []
    assert calls["n"] == 0, "a file with neither the literal symbol nor an alias must not parse"


def test_early_exit_rust_symbol_absent_skips_parse_entirely(tmp_path, monkeypatch) -> None:
    target = _write(
        tmp_path / "leaf.rs",
        "fn unrelated_thing() -> i32 {\n    1\n}\n",
    )
    calls = _spy_parse_calls(monkeypatch)

    references, matched_calls = repo_map._rust_references_and_calls(target, "create_invoice")

    assert references == []
    assert matched_calls == []
    assert calls["n"] == 0


def test_early_exit_aliased_re_export_still_parses_once_and_resolves(tmp_path, monkeypatch) -> None:
    """The trap: an aliased barrel re-export (`export {x as y} from "./mod"`) means the target
    symbol's literal name never appears in the consuming file's text at all -- the early exit
    must still fall through to parsing because alias_names is non-empty (golden-parity anchor:
    test_js_ts_advanced_resolution.py::test_aliased_re_export_chain_resolves_to_original_definition).
    """
    project = tmp_path / "project"
    src_dir = project / "src"
    _write(
        src_dir / "payments.ts",
        "export function createInvoice(total) {\n  return total + 1;\n}\n",
    )
    _write(
        src_dir / "barrel.ts",
        'export { createInvoice as makeInvoice } from "./payments";\n',
    )
    service_path = _write(
        src_dir / "service.ts",
        'import { makeInvoice } from "./barrel";\n\n'
        "export function buildReceipt(total) {\n"
        "  return makeInvoice(total);\n"
        "}\n",
    )
    assert "createInvoice" not in service_path.read_text(encoding="utf-8")

    calls = _spy_parse_calls(monkeypatch)
    references, _ = repo_map._js_ts_references_and_calls(service_path, "createInvoice", project)
    assert calls["n"] > 0, "an alias-only match must still force at least one parse"
    parses_after_first_call = calls["n"]
    assert len(references) == 1
    assert references[0]["line"] == 4
    assert "re-export-chain" in references[0].get("resolution_provenance", [])

    # Everything the chain resolution touched (service.ts itself, plus barrel.ts/payments.ts
    # visited while resolving the "makeInvoice" -> "createInvoice" re-export) is now warm in the
    # shared parse-product cache -- an identical repeat call must not re-parse ANY of them.
    repo_map._js_ts_references_and_calls(service_path, "createInvoice", project)
    assert calls["n"] == parses_after_first_call, "warm cache: repeat call must not re-parse"

    # End-to-end: the public caller-scan surfaces the same resolved caller.
    callers_payload = repo_map.build_symbol_callers("createInvoice", str(project))
    service_callers = [
        entry
        for entry in callers_payload["callers"]
        if _normalize(entry["file"]).endswith("src/service.ts")
    ]
    assert len(service_callers) == 1
    assert service_callers[0]["line"] == 4
    assert "re-export-chain" in service_callers[0]["resolution_provenance"]


# ---------------------------------------------------------------------------
# (4) gate: _file_may_import_symbol_definition's sound JS/TS branch
# ---------------------------------------------------------------------------


def test_gate_comment_only_import_marker_returns_false(tmp_path) -> None:
    path = _write(
        tmp_path / "leaf.ts",
        "// an import used to live here\nexport function noop() {}\n",
    )
    definition_file = str(tmp_path / "def.ts")
    assert repo_map._file_may_import_symbol_definition(path, [definition_file]) is False


def test_gate_export_only_barrel_returns_false(tmp_path) -> None:
    path = _write(
        tmp_path / "barrel.ts",
        'export { createInvoice } from "./payments";\n',
    )
    definition_file = str(tmp_path / "payments.ts")
    assert repo_map._file_may_import_symbol_definition(path, [definition_file]) is False


def test_gate_true_for_each_import_binding_kind(tmp_path) -> None:
    named = _write(tmp_path / "named.ts", 'import { createInvoice } from "./payments";\n')
    default = _write(tmp_path / "default.ts", 'import createInvoice from "./payments";\n')
    namespace = _write(tmp_path / "namespace.ts", 'import * as payments from "./payments";\n')
    definition_file = str(tmp_path / "payments.ts")
    for path in (named, default, namespace):
        assert repo_map._file_may_import_symbol_definition(path, [definition_file]) is True


def test_gate_true_for_barrel_reexport_consumer_despite_no_definition_file_alias(
    tmp_path,
) -> None:
    """The naive-mirror trap this gate design avoids: _module_aliases_for_path on the
    DEFINITION file ("payments.ts") yields "payments"-derived aliases, none of which appear
    anywhere in service.ts's text (it only ever mentions "./barrel"). A naive mirror of the
    non-JS/TS branch would return False here and drop the caller; the sound gate instead checks
    for ANY real import binding, which service.ts has (`import { makeInvoice } from "./barrel"`).
    """
    project = tmp_path / "project"
    src_dir = project / "src"
    _write(
        src_dir / "payments.ts",
        "export function createInvoice(total) {\n  return total + 1;\n}\n",
    )
    _write(
        src_dir / "barrel.ts",
        'export { createInvoice as makeInvoice } from "./payments";\n',
    )
    service_path = _write(
        src_dir / "service.ts",
        'import { makeInvoice } from "./barrel";\n\n'
        "export function buildReceipt(total) {\n"
        "  return makeInvoice(total);\n"
        "}\n",
    )
    definition_file = str(src_dir / "payments.ts")
    assert repo_map._file_may_import_symbol_definition(service_path, [definition_file]) is True


# ---------------------------------------------------------------------------
# (5) daemon flush
# ---------------------------------------------------------------------------


def test_new_caches_registered_in_mtime_clear_registry() -> None:
    assert repo_map._read_source_text_cached_bounded.cache_clear in (
        repo_map._MTIME_CACHE_CLEAR_REGISTRY
    )
    assert repo_map._parsed_source_and_tree_bounded.cache_clear in (
        repo_map._MTIME_CACHE_CLEAR_REGISTRY
    )


def test_clear_all_source_caches_sweeps_parse_product_cache(tmp_path, monkeypatch) -> None:
    target = tmp_path / "mod.ts"
    target.write_text(
        "export function createInvoice(total) {\n  return total + 1;\n}\n", encoding="utf-8"
    )

    frozen_key = (123456789, 999)
    monkeypatch.setattr(repo_map, "_mtime_key", lambda path_str: frozen_key)

    first = repo_map._parsed_source_and_tree(str(target))
    assert first is not None
    first_source = first[0]

    # Same byte length, frozen mtime key: the pathological same-key-edit case a warm daemon
    # can hit in practice.
    target.write_text(
        "export function createInvoice(total) {\n  return total + 9;\n}\n", encoding="utf-8"
    )
    still_stale = repo_map._parsed_source_and_tree(str(target))
    assert still_stale is not None
    assert still_stale[0] == first_source, "sanity: frozen key must serve the stale cached parse"

    repo_map._clear_all_source_caches()
    fresh = repo_map._parsed_source_and_tree(str(target))
    assert fresh is not None
    assert fresh[0] != first_source, "_clear_all_source_caches() must sweep the parse-product cache"


# ---------------------------------------------------------------------------
# (6) oversize bypass
# ---------------------------------------------------------------------------


def test_oversize_file_bypasses_parse_product_cache(tmp_path, monkeypatch) -> None:
    big = tmp_path / "big.ts"
    content = "export function createInvoice(total) {\n  return total + 1;\n}\n"
    big.write_text(content, encoding="utf-8")
    # Force the "too large to cache" branch without needing a real multi-MB fixture.
    monkeypatch.setattr(repo_map, "_SYMBOL_LITERAL_SEED_MAX_BYTES", 10)

    calls = _spy_parse_calls(monkeypatch)

    first = repo_map._parsed_source_and_tree(str(big))
    second = repo_map._parsed_source_and_tree(str(big))

    assert first is not None
    assert second is not None
    assert first[0] == second[0] == content
    # Bypassed the cache both times -- a giant file must never sit in the parse-product cache.
    assert calls["n"] == 2


def test_references_use_parsed_source_lines_not_a_separate_stale_text_read(
    tmp_path: Path, monkeypatch
) -> None:
    # C3 (audit): `_js_ts_references_and_calls` reads the source TEXT once (cheap pre-parse gate)
    # and the parse PRODUCT (source+bytes+tree) via a SECOND independent (path, mtime, size) cache
    # lookup. `lines` (used for a reference's "text" line content) must come from the SAME read as
    # the tree, or a file edited between the two lookups makes tree line-indices index into stale
    # `lines` -> wrong reported line content. Simulate the skew: freeze the parse product on the
    # real file while the text read returns a line-shifted stale copy; the reference text must match
    # the PARSED source (correct), not the stale text read (blank-shifted).
    source_file = tmp_path / "widget.ts"
    real_source = "const total = computeWidgetTotal();\ncomputeWidgetTotal();\n"
    source_file.write_text(real_source, encoding="utf-8")

    # Stale text read: same symbol present (so the symbol-absent early-exit still falls through),
    # but two blank lines prepended -> every line index is shifted by 2 vs the real parsed source.
    stale_source = "\n\n" + real_source
    monkeypatch.setattr(repo_map, "_read_source_text_cached", lambda _p: stale_source)

    references, _calls = repo_map._js_ts_references_and_calls(source_file, "computeWidgetTotal")

    assert references, "expected at least one reference to computeWidgetTotal"
    # With the fix, line content comes from the parsed source (real file) -> the actual code line.
    # With the bug (lines from the stale text read), index 0 would be the prepended blank line "".
    for ref in references:
        assert "computeWidgetTotal" in ref["text"], (
            f"reference text {ref['text']!r} came from the stale text read, not the parsed source"
        )
