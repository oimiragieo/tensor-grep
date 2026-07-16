"""Governance test for docs/gpu_crossover.md's "Supported semantics" tables.

Audit #79/#10/#14 finding: the doc's single "Supported semantics" table conflated two
independent GPU-adjacent lanes and got several rows factually backwards --
`--count`/`--hidden`/`--no-ignore` were documented as "Not supported" even though the
native-lane source of truth (`gpu_native_fallback_reason` in `rust_core/src/main.rs`)
never checks any of them, so they route straight to the native CUDA kernel.

This test pins the doc against the real code from both directions:

  * Native CUDA-kernel lane -- `gpu_native_fallback_reason` is a private Rust function,
    so Python cannot import it. Its exact reason-set is pinned below as a canonical
    literal set (mirrored 1:1, in the function's own `if`/`else` order, from
    `rust_core/src/main.rs`). A companion Rust test
    (`gpu_native_fallback_reason_reason_set_matches_documented_contract` in that file's
    `mod tests`) pins the SAME set from the code side -- if you touch
    `gpu_native_fallback_reason`, update BOTH pinned sets AND the doc table, or one of
    the two tests will fail.

  * Python GPU sidecar lane -- `Pipeline._should_honor_explicit_gpu_ids` /
    `Pipeline._needs_python_cpu` (`src/tensor_grep/core/pipeline.py`) are pure Python, so
    this test imports and calls them directly (real introspection, not a second
    hand-copied guess) and cross-checks the live result against the doc table.

Mirrors the flat, `Path.read_text` + plain-assert style of
`tests/unit/test_routing_policy_docs.py`, with row-level table parsing added so a doc
edit that silently reverses a Supported/Not-supported verdict on one row cannot hide
behind an unrelated substring match elsewhere in the file.
"""

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
DOC_PATH = _REPO_ROOT / "docs" / "gpu_crossover.md"

NATIVE_LANE_HEADING = "### Native CUDA-kernel lane"
SIDECAR_LANE_HEADING = "### Python GPU sidecar lane"
HISTORICAL_HEADING = "## Historical v1.7 Artifact (Superseded)"

# Pinned 1:1, in code order, against the `if`/`else` chain of `gpu_native_fallback_reason`
# (`rust_core/src/main.rs`, near line 10647 as of this writing). This is the exact set of
# reason strings the function can return for `Some(reason)`; `None` means "supported".
NATIVE_LANE_NOT_SUPPORTED_REASONS = (
    "case-insensitive searches are not yet supported by native GPU routing",
    "binary-as-text searches are not yet supported by native GPU routing",
    "line-terminator patterns require CPU or sidecar routing",
    "invert-match searches are not yet supported by native GPU routing",
    "context line searches are not yet supported by native GPU routing",
    "max-count searches are not yet supported by native GPU routing",
    "word-boundary searches are not yet supported by native GPU routing",
    "regex patterns still require the Python GPU sidecar",
    "--replace searches are not yet supported by native GPU routing",
    "--only-matching searches are not yet supported by native GPU routing",
    "--max-filesize is not yet supported by native GPU routing",
    "--color is not yet supported by native GPU routing",
    "--no-ignore-vcs is not yet supported by native GPU routing",
)

# Flags `gpu_native_fallback_reason` never checks at all -- confirmed supported by the
# native kernel because they are threaded into `GpuNativeSearchConfig` /
# `execute_gpu_native_route` instead (same file). Table-row label substrings, matched
# against the native-lane table specifically so a stray mention elsewhere in the doc
# cannot satisfy the check.
NATIVE_LANE_CONFIRMED_SUPPORTED_ROW_LABELS = (
    "Count / counting mode (`-c`, `--count`)",
    "Hidden-file or no-ignore overrides (`--hidden`, `--no-ignore`)",
)

FIXED_STRING_ROW_LABEL = "Fixed-string multi-pattern"


def _section(doc: str, start_heading: str, end_heading: str) -> str:
    start = doc.index(start_heading)
    end = doc.index(end_heading, start)
    return doc[start:end]


def _native_lane_section(doc: str) -> str:
    return _section(doc, NATIVE_LANE_HEADING, SIDECAR_LANE_HEADING)


def _sidecar_lane_section(doc: str) -> str:
    return _section(doc, SIDECAR_LANE_HEADING, HISTORICAL_HEADING)


def _table_rows(section: str) -> list[str]:
    return [line for line in section.splitlines() if line.strip().startswith("|")]


def _rows_matching(section: str, label: str) -> list[str]:
    return [row for row in _table_rows(section) if label in row]


def test_doc_splits_native_and_sidecar_lanes_into_separate_tables() -> None:
    doc = DOC_PATH.read_text(encoding="utf-8")

    assert NATIVE_LANE_HEADING in doc
    assert SIDECAR_LANE_HEADING in doc
    # The native-lane heading must precede the sidecar-lane heading, and both must
    # precede the historical section, or `_native_lane_section`/`_sidecar_lane_section`
    # would silently slice the wrong span.
    assert (
        doc.index(NATIVE_LANE_HEADING)
        < doc.index(SIDECAR_LANE_HEADING)
        < doc.index(HISTORICAL_HEADING)
    )


def test_native_lane_cites_its_source_of_truth_function() -> None:
    doc = DOC_PATH.read_text(encoding="utf-8")
    section = _native_lane_section(doc)

    assert "gpu_native_fallback_reason" in section
    assert "rust_core/src/main.rs" in section


def test_native_lane_table_documents_every_fallback_reason() -> None:
    doc = DOC_PATH.read_text(encoding="utf-8")
    section = _native_lane_section(doc)

    for reason in NATIVE_LANE_NOT_SUPPORTED_REASONS:
        assert reason in section, f"native-lane table missing documented reason: {reason!r}"


def test_native_lane_table_does_not_understate_native_support() -> None:
    """Regression guard for the exact audit #79/#10/#14 bug: `--count`, `--hidden`, and
    `--no-ignore` were documented as "Not supported" even though
    `gpu_native_fallback_reason` never inspects any of them."""
    doc = DOC_PATH.read_text(encoding="utf-8")
    section = _native_lane_section(doc)

    for row_label in NATIVE_LANE_CONFIRMED_SUPPORTED_ROW_LABELS:
        rows = _rows_matching(section, row_label)
        assert rows, f"native-lane table missing a row for: {row_label}"
        for row in rows:
            assert "Not supported" not in row, f"{row_label} wrongly marked unsupported: {row!r}"
            assert "Supported" in row, f"{row_label} row has no Supported verdict: {row!r}"


def test_native_lane_declares_fixed_string_multipattern_supported() -> None:
    doc = DOC_PATH.read_text(encoding="utf-8")
    section = _native_lane_section(doc)

    rows = _rows_matching(section, FIXED_STRING_ROW_LABEL)
    assert rows, "native-lane table missing the fixed-string multi-pattern row"
    for row in rows:
        assert "Supported" in row
        assert "Not supported" not in row


def test_native_lane_table_marks_every_pinned_reason_row_not_supported() -> None:
    """Belt-and-braces: every reason-bearing row must actually say "Not supported" (not
    just mention the reason string in passing prose)."""
    doc = DOC_PATH.read_text(encoding="utf-8")
    section = _native_lane_section(doc)

    for reason in NATIVE_LANE_NOT_SUPPORTED_REASONS:
        rows = [row for row in _table_rows(section) if reason in row]
        assert rows, f"reason not present in a table row (found only in prose?): {reason!r}"
        for row in rows:
            assert "Not supported" in row, f"reason row missing a Not-supported verdict: {row!r}"


def test_sidecar_lane_table_matches_live_pipeline_introspection() -> None:
    """Import + introspect the real `Pipeline` routing decision -- this half of the
    contract IS pure Python (unlike the native lane), so cross-check the doc against
    live code instead of a second hand-copied guess that could itself drift."""
    from tensor_grep.core.config import SearchConfig
    from tensor_grep.core.pipeline import Pipeline

    def honors_explicit_gpu(**overrides: object) -> bool:
        config = SearchConfig(gpu_device_ids=[0], **overrides)  # type: ignore[arg-type]
        needs_python_cpu = Pipeline._needs_python_cpu(config)
        return Pipeline._should_honor_explicit_gpu_ids(config, needs_python_cpu)

    live_support = {
        "ast": honors_explicit_gpu(ast=True),
        "count": honors_explicit_gpu(count=True),
        "fixed_strings": honors_explicit_gpu(fixed_strings=True),
        "context": honors_explicit_gpu(context=3),
        "word_regexp": honors_explicit_gpu(word_regexp=True),
        "line_regexp": honors_explicit_gpu(line_regexp=True),
        "ltl": honors_explicit_gpu(ltl=True),
        "plain_regex": honors_explicit_gpu(),
    }

    # Ground truth computed from the real Pipeline right now, so this test breaks (not
    # the doc-parsing tests below) the moment `pipeline.py`'s own routing logic changes.
    assert live_support == {
        "ast": False,
        "count": False,
        "fixed_strings": False,
        "context": False,
        "word_regexp": False,
        "line_regexp": False,
        "ltl": False,
        "plain_regex": True,
    }

    doc = DOC_PATH.read_text(encoding="utf-8")
    section = _sidecar_lane_section(doc)

    for unsupported_label in (
        "AST search (`--ast`)",
        "Count mode (`-c`, `--count`)",
        "Fixed-string patterns (`-F`)",
        "Context, line-regexp, word-regexp, or LTL queries",
    ):
        rows = _rows_matching(section, unsupported_label)
        assert rows, f"sidecar-lane table missing a row for: {unsupported_label}"
        for row in rows:
            assert "Not supported" in row, f"{unsupported_label} row: {row!r}"

    supported_rows = _rows_matching(section, "General regex")
    assert supported_rows, "sidecar-lane table missing the general-regex row"
    for row in supported_rows:
        assert "Supported" in row
        assert "Not supported" not in row


def test_sidecar_lane_documents_fail_closed_contract() -> None:
    doc = DOC_PATH.read_text(encoding="utf-8")
    section = _sidecar_lane_section(doc)

    assert "ConfigurationError" in section
    assert "fails closed" in section.lower()
    # The sidecar lane must not be described with the native lane's silent-fallback
    # framing -- an explicit `--gpu-device-ids` request it can't honor is a hard refusal,
    # not a quiet CPU fallback that could be mistaken for GPU acceleration proof.
    assert "src/tensor_grep/core/pipeline.py" in section
