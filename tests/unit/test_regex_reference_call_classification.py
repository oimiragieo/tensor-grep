"""The regex reference fallback must not classify a DEFINITION line as a call (task 326).

Found by an external codex dogfood of the published v1.98.27 wheel:

    tg refs . collect_walked_files --json
    -> {"line": 3879,
        "text": "fn collect_walked_files(config: &GpuNativeSearchConfig, ...) -> ... {",
        "ref_kind": "call", "provenance": "regex-heuristic", "resolution_confidence": 1.0}

That line is the declaration, not a call site. The tree-sitter Rust path already refuses to
count it -- ``_is_definition_identifier`` (``repo_map.py``) excludes ``function_item`` /
``struct_item`` / ``enum_item`` / ``trait_item``. The regex fallback had no such guard, so this
is a TWIN GAP: one arm of the same feature carried the check and the other did not.

Reachability is not theoretical. ``_rust_references_and_calls`` returns ``([], [])`` when no
tree-sitter Rust grammar is installed, and the caller then falls through to
``_regex_references_and_calls`` and promotes every ``call`` row to a reference with
``ref_kind="call"``. A plain ``pip``/``uvx`` install ships no Rust grammar, so the regex arm is
what real users hit -- which is exactly how the external reviewer reached it.

Each test below asserts BOTH arms. A guard that suppressed everything would pass a
definition-only assertion just as happily as a correct one, so every definition case is paired
with a real call site that MUST still be classified as a call.
"""

from __future__ import annotations

from pathlib import Path

from tensor_grep.cli.repo_map import _regex_references_and_calls


def _call_lines(tmp_path: Path, filename: str, source: str, symbol: str) -> set[int]:
    """Line numbers ``_regex_references_and_calls`` classified as CALLS."""
    path = tmp_path / filename
    path.write_text(source, encoding="utf-8")
    _references, calls = _regex_references_and_calls(path, symbol)
    return {int(call["line"]) for call in calls}


def _reference_lines(tmp_path: Path, filename: str, source: str, symbol: str) -> set[int]:
    path = tmp_path / filename
    path.write_text(source, encoding="utf-8")
    references, _calls = _regex_references_and_calls(path, symbol)
    return {int(reference["line"]) for reference in references}


def test_rust_fn_definition_is_not_a_call_but_the_real_call_site_still_is(
    tmp_path: Path,
) -> None:
    source = "\n".join((
        "fn collect_walked_files(config: &Config) -> Result<Vec<PathBuf>> {",  # 1: definition
        "    Ok(Vec::new())",  # 2
        "}",  # 3
        "fn caller(config: &Config) {",  # 4
        "    let files = collect_walked_files(config);",  # 5: real call
        "}",  # 6
    ))

    calls = _call_lines(tmp_path, "walk.rs", source, "collect_walked_files")

    # Control arm FIRST: if the guard over-fires and suppresses everything, this fails and the
    # definition assertion below stops being evidence of anything.
    assert 5 in calls, f"the real call site must still be classified as a call; got {calls}"
    assert 1 not in calls, f"`fn NAME(` is a declaration, not a call; got {calls}"


def test_rust_definition_modifiers_do_not_smuggle_a_definition_past_the_guard(
    tmp_path: Path,
) -> None:
    """Only the token immediately before the symbol is inspected, so every `fn` modifier
    combination collapses to the same check."""
    source = "\n".join((
        "pub fn collect_walked_files(c: &C) -> R {}",  # 1
        "pub(crate) fn collect_walked_files(c: &C) -> R {}",  # 2
        "async fn collect_walked_files(c: &C) -> R {}",  # 3
        "pub async unsafe fn collect_walked_files(c: &C) -> R {}",  # 4
        "const fn collect_walked_files(c: &C) -> R {}",  # 5
        'pub extern "C" fn collect_walked_files(c: &C) -> R {}',  # 6
        "    let files = collect_walked_files(config);",  # 7: real call
    ))

    calls = _call_lines(tmp_path, "modifiers.rs", source, "collect_walked_files")

    assert calls == {7}, f"only line 7 is a call site; got {sorted(calls)}"


def test_js_ts_function_definition_is_not_a_call_but_the_real_call_site_still_is(
    tmp_path: Path,
) -> None:
    """`_regex_references_and_calls` gates on `_JS_TS_SUFFIXES | _RUST_SUFFIXES`, so the same
    defect reached JS/TS via `function NAME(`. Fixing only the Rust spelling would have left
    the twin live."""
    source = "\n".join((
        "function collectWalkedFiles(config) {",  # 1: definition
        "  return [];",  # 2
        "}",  # 3
        "export function collectWalkedFiles2(config) {}",  # 4
        "async function collectWalkedFiles3(config) {}",  # 5
        "const files = collectWalkedFiles(config);",  # 6: real call
    ))

    calls = _call_lines(tmp_path, "walk.js", source, "collectWalkedFiles")

    assert 6 in calls, f"the real call site must still be classified as a call; got {calls}"
    assert 1 not in calls, f"`function NAME(` is a declaration, not a call; got {calls}"


def test_js_generator_function_definition_is_not_a_call(tmp_path: Path) -> None:
    source = "\n".join((
        "function* collectWalkedFiles(config) {",  # 1: generator definition
        "  yield 1;",  # 2
        "}",  # 3
        "const files = collectWalkedFiles(config);",  # 4: real call
    ))

    calls = _call_lines(tmp_path, "gen.js", source, "collectWalkedFiles")

    assert calls == {4}, f"only line 4 is a call site; got {sorted(calls)}"


def test_the_definition_line_is_still_reported_as_a_REFERENCE(tmp_path: Path) -> None:
    """The fix narrows CALL classification only. A declaration is a legitimate reference to the
    symbol and `tg refs` must keep reporting it -- dropping it would trade a mislabel for a
    missing row, which is strictly worse for impact analysis."""
    source = "\n".join((
        "fn collect_walked_files(config: &Config) -> R {",  # 1: definition
        "}",  # 2
        "    let files = collect_walked_files(config);",  # 3: real call
    ))

    references = _reference_lines(tmp_path, "refs.rs", source, "collect_walked_files")

    assert references == {1, 3}, (
        f"the declaration must remain a reference row, not be dropped; got {sorted(references)}"
    )


def test_a_call_whose_name_merely_ends_in_fn_is_not_mistaken_for_a_definition(
    tmp_path: Path,
) -> None:
    """A textual `fn`/`function` check that is not token-anchored would swallow a real call to a
    symbol preceded by an identifier ending in those letters."""
    source = "\n".join((
        "    let files = spawn_fn collect_walked_files(config);",  # 1: not a definition
        "    other.collect_walked_files(config);",  # 2: method call
        "    Self::collect_walked_files(config);",  # 3: path call
    ))

    calls = _call_lines(tmp_path, "boundary.rs", source, "collect_walked_files")

    assert calls == {1, 2, 3}, (
        f"`fn` must match as a whole token, not a suffix; got {sorted(calls)}"
    )
