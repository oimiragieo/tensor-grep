"""Native-tg delegation must never silently drop an output-shaping SearchConfig field.

Background (round-4 #25, council-vetted 2026-07-03): `_can_delegate_to_native_tg_search`
hands the ENTIRE search to the native `tg` subprocess, which then ``sys.exit``s
(main.py call site ~6146) BEFORE the Python-side BM25 rerank (main.py ~6363) and the
in-backend sort. Any output-affecting config field that is neither forwarded to the native
argv nor forces a refuse-to-delegate is silently dropped -> wrong output that reads as
absence. `rank_bm25` and `sort_files` were exactly that hole; this locks them shut and
adds a ratchet so a future field cannot re-open the class (same class as the -u/-uu no-op
fixed in #336).
"""

import ast
import dataclasses
import inspect

from tensor_grep.cli import main as tg_main
from tensor_grep.core.config import SearchConfig

# Pre-existing uncovered SearchConfig fields (as of #25). These were ALREADY dropped
# through delegation before this PR; they are acknowledged tech debt, NOT blessed as
# safe-to-forward. Listing them keeps the coverage ratchet green today while making a
# NEW unclassified field fail loudly. Grouped for a future forward-or-refuse audit.
_NATIVE_TG_DELEGATION_KNOWN_GAP_FIELDS = frozenset({
    # AST structural-search mode — inert on this format_type == "rg" text path.
    "ast_prefer_native",
    "ast_selector",
    "ast_stdin",
    "ast_stdin_input",
    "ast_strictness",
    # NLP / engine-selection / internal telemetry — not output-shaping on this path.
    "nlp_threshold",
    "use_jit",
    "query_pattern",  # auto-set to the pattern on EVERY search (main.py ~6045);
    # a runtime "differs-from-default" gate would trip on this and kill the fast path.
    "input_total_bytes",  # set AFTER the gate; 0 at decision time.
    # Case / ignore-scope semantics — likely inert (native defaults already match)
    # but NOT re-audited here; candidates for a follow-up forward-or-refuse pass.
    "case_sensitive",
    "ignore_dot",
    "ignore_exclude",
    "ignore_files",
    "ignore_global",
    "ignore_messages",
    "ignore_parent",
    "ignore_vcs",
    # Explicit "--no-*" double-negation flags — inert when the native default already
    # matches the negated state (the common case); tracked, not forwarded.
    "no_auto_hybrid_regex",
    "no_binary",
    "no_block_buffered",
    "no_byte_offset",
    "no_column",
    "no_context_separator",
    "no_crlf",
    "no_encoding",
    "no_fixed_strings",
    "no_follow",
    "no_glob_case_insensitive",
    "no_ignore_file_case_insensitive",
    "no_include_zero",
    "no_invert_match",
    "no_json",
    "no_line_buffered",
    "no_max_columns_preview",
    "no_mmap",
    "no_multiline",
    "no_multiline_dotall",
    "no_one_file_system",
    "no_pcre2",
    "no_pcre2_unicode",
    "no_pre",
    "no_search_zip",
    "no_stats",
    "no_text",
    "no_trim",
})

# The gate refuses to delegate whenever the CLI requested a files-listing mode; it reads
# these off explicit keyword arguments rather than the config dataclass, so the config
# fields of the same name are covered by the gate itself.
_GATE_HANDLED_FIELDS = frozenset({"files_with_matches", "files_without_match"})


def _forwarded_config_fields() -> set[str]:
    """Fields `_build_native_tg_search_command` reads off `config`, derived from its
    source via AST so this can never drift from a hand-maintained second list."""
    source = inspect.getsource(tg_main._build_native_tg_search_command)
    return {
        node.attr
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "config"
    }


def _gate(config: SearchConfig, **overrides: object) -> bool:
    kwargs: dict[str, object] = {
        "ndjson": False,
        "files_mode": False,
        "files_with_matches": False,
        "files_without_match": False,
        "format_type": "rg",
    }
    kwargs.update(overrides)
    return tg_main._can_delegate_to_native_tg_search(config, **kwargs)  # type: ignore[arg-type]


class TestRefusesOutputShapingFields:
    def test_rank_bm25_refuses_delegation(self) -> None:
        # --rank: native tg has no BM25 (routes --rank back to the Python sidecar);
        # delegating a --rank search would drop the rerank entirely -> unranked output.
        assert _gate(SearchConfig(rank_bm25=True, force_cpu=True)) is False

    def test_sort_files_refuses_delegation(self) -> None:
        assert _gate(SearchConfig(sort_files=True, force_cpu=True)) is False

    def test_rank_bm25_refuses_under_json_trigger(self) -> None:
        assert _gate(SearchConfig(rank_bm25=True, json_mode=True)) is False

    def test_rank_bm25_refuses_under_ndjson_trigger(self) -> None:
        assert _gate(SearchConfig(rank_bm25=True), ndjson=True) is False

    def test_sort_files_refuses_under_gpu_trigger(self) -> None:
        assert _gate(SearchConfig(sort_files=True, gpu_device_ids=[0])) is False


class TestDefaultFastPathPreserved:
    """The 2026-06-30 #1 receipt: a naive guard broke the default delegation path.
    rank_bm25/sort_files default False, so existing default-path callers are untouched."""

    def test_plain_cpu_still_delegates(self) -> None:
        assert _gate(SearchConfig(force_cpu=True)) is True

    def test_plain_json_still_delegates(self) -> None:
        assert _gate(SearchConfig(json_mode=True)) is True

    def test_plain_gpu_still_delegates(self) -> None:
        assert _gate(SearchConfig(gpu_device_ids=[0])) is True

    def test_plain_ndjson_still_delegates(self) -> None:
        assert _gate(SearchConfig(), ndjson=True) is True


class TestFieldCoverageRatchet:
    def test_every_field_classified(self) -> None:
        all_fields = {f.name for f in dataclasses.fields(SearchConfig)}
        forwarded = _forwarded_config_fields()
        required = set(tg_main._NATIVE_TG_DELEGATION_DEFAULT_REQUIRED_FIELDS)
        covered = (
            forwarded | required | _GATE_HANDLED_FIELDS | _NATIVE_TG_DELEGATION_KNOWN_GAP_FIELDS
        )
        uncovered = sorted(all_fields - covered)
        assert not uncovered, (
            f"SearchConfig field(s) {uncovered} are neither forwarded to the native argv "
            f"(_build_native_tg_search_command), in the refuse-tuple "
            f"(_NATIVE_TG_DELEGATION_DEFAULT_REQUIRED_FIELDS), gate-handled, nor in the "
            f"documented KNOWN_GAP set. A new output-affecting field silently dropped "
            f"through native delegation is the recurring flag-drop bug class (#336 -u, "
            f"#25 rank/sort). Classify it: forward it, refuse on it, or add it to KNOWN_GAP."
        )

    def test_rank_and_sort_are_refused_not_gapped(self) -> None:
        required = set(tg_main._NATIVE_TG_DELEGATION_DEFAULT_REQUIRED_FIELDS)
        assert "rank_bm25" in required
        assert "sort_files" in required
        assert "rank_bm25" not in _NATIVE_TG_DELEGATION_KNOWN_GAP_FIELDS
        assert "sort_files" not in _NATIVE_TG_DELEGATION_KNOWN_GAP_FIELDS

    def test_known_gap_has_no_stale_entries(self) -> None:
        # Guard the ratchet from the other side: a KNOWN_GAP entry that later gets
        # forwarded/refused/removed must be pruned, or it silently masks a real gap.
        all_fields = {f.name for f in dataclasses.fields(SearchConfig)}
        forwarded = _forwarded_config_fields()
        required = set(tg_main._NATIVE_TG_DELEGATION_DEFAULT_REQUIRED_FIELDS)
        stale = sorted(
            f
            for f in _NATIVE_TG_DELEGATION_KNOWN_GAP_FIELDS
            if f not in all_fields or f in forwarded or f in required
        )
        assert not stale, f"stale KNOWN_GAP entries (now covered elsewhere or removed): {stale}"
