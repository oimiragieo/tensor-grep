"""`_references_and_calls_for_path` must dispatch through the REGISTRY, never a language ladder.

WHY THIS EXISTS (plan Task 9, Step 2). The seam once branched on ``language_id`` in two
hard-coded ladders. The F7 language campaign (Tasks 10A-10E) removed that branching as a side
effect of registering a real ``references_and_calls`` for all ten languages -- but nothing PINNED
the result, so a future change could quietly restore a ladder and every existing test would stay
green. A refactor with no guard is a state, not a contract.

WHAT THIS DOES NOT CLAIM. Passing here means the seam CONSULTS the registry, not that any
particular language's extractor is correct -- those live in the per-language suites.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from tensor_grep.cli import lang_registry, repo_map

_SYNTHETIC_SUFFIX = ".tgsynthetic"
_SYNTHETIC_ID = "tgsynthetic"


@pytest.fixture
def synthetic_language() -> Any:
    """Register a throwaway language whose extractor records that it was called.

    Registration is idempotent by ``language_id`` (lang_registry.register_language), and the
    registry is a module-level dict, so the fixture removes the entry afterwards. Without that
    teardown a later test in the same session would see a language that does not exist -- the
    shared-mutable-state trap.
    """
    calls: list[tuple[Path, str]] = []

    def _spy(
        path: Path,
        symbol: str,
        repo_root: Path | str | None = None,
        *,
        definition_dirs: frozenset[str] | None = None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        # Signature pinned to the seam's real adapter call, discovered by running this test
        # against the product: `spec.references_and_calls(path, symbol, repo_root,
        # definition_dirs=definition_dirs)`. A **kwargs-only spy silently accepts a CHANGED
        # contract, so it would keep passing after the adapter grew or dropped an argument --
        # the check that cannot fail. Spelling every parameter makes this test break loudly if
        # the adapter signature moves, which is the point.
        calls.append((path, symbol))
        return ([{"line": 1, "sentinel": "from-registry"}], [])

    spec = lang_registry.LanguageSpec(
        language_id=_SYNTHETIC_ID,
        suffixes=frozenset({_SYNTHETIC_SUFFIX}),
        grammar_modules=(),
        parser_for_path=lambda _path: None,
        provenance_when_parsed="tree-sitter",
        provenance_when_missing="grammar-missing",
        import_markers=(),
        references_and_calls=_spy,
    )
    registry_snapshot = dict(lang_registry.LANGUAGE_REGISTRY)
    suffix_snapshot = dict(lang_registry._SPEC_BY_SUFFIX)
    lang_registry.register_language(spec)
    try:
        yield calls
    finally:
        # Restore BOTH maps. register_language writes LANGUAGE_REGISTRY *and* the derived
        # _SPEC_BY_SUFFIX; popping only the first leaves `.tgsynthetic -> spec` behind, and the
        # leak is invisible here -- it surfaces in test_lang_registry's suffix-union pin, a
        # DIFFERENT file. That is exactly what happened while writing this test, in the fixture
        # whose own docstring warned about shared mutable state.
        lang_registry.LANGUAGE_REGISTRY.clear()
        lang_registry.LANGUAGE_REGISTRY.update(registry_snapshot)
        lang_registry._SPEC_BY_SUFFIX.clear()
        lang_registry._SPEC_BY_SUFFIX.update(suffix_snapshot)


def test_dispatch_invokes_the_registered_extractor_not_the_regex_fallback(
    tmp_path: Path, synthetic_language: list[tuple[Path, str]]
) -> None:
    """The registered spy must be reached, and its rows must be what comes back.

    Asserting on the SENTINEL row rather than merely on call-count matters: a seam that called
    the extractor and then discarded its result in favour of the regex fallback would pass a
    count-only check.
    """
    target = tmp_path / f"sample{_SYNTHETIC_SUFFIX}"
    target.write_text("anything\n", encoding="utf-8")

    refs, _calls = repo_map._references_and_calls_for_path(target, "whatever")

    assert synthetic_language, (
        "the registered references_and_calls was never invoked -- the seam fell through to the "
        "regex fallback, i.e. it is no longer registry-driven"
    )
    assert refs and refs[0].get("sentinel") == "from-registry", (
        f"the seam invoked the extractor but did not return its rows: {refs!r}"
    )


def test_unregistered_suffix_still_falls_back_without_raising(tmp_path: Path) -> None:
    """CONTROL: the fallback path must remain reachable and non-raising.

    Without this, the test above could be satisfied by a seam that dispatches correctly for
    registered languages and CRASHES for everything else -- a guard that passes for the wrong
    reason. `.tgunregistered` has no LanguageSpec by construction.
    """
    target = tmp_path / "sample.tgunregistered"
    target.write_text("anything\n", encoding="utf-8")

    refs, calls = repo_map._references_and_calls_for_path(target, "whatever")

    assert isinstance(refs, list) and isinstance(calls, list)


def test_the_seam_contains_no_string_comparison_at_all() -> None:
    """STRUCTURAL guard: the dispatch body must compare against NO string constant.

    An earlier cut of this test looked for registered ``language_id`` values as string literals.
    It did not fire when a ladder was injected -- because the injected branch compared a SUFFIX
    (``path.suffix == ".py"``), not a language id. A guard that enumerates one vocabulary misses
    every other spelling of the same defect; this repo has a dated law for exactly that shape, and
    it landed on this guard while the guard was being written.

    So this asserts STRUCTURE instead of vocabulary: after removing the docstring, the body must
    contain zero ``ast.Compare`` nodes against a string constant. Any ladder -- by language id, by
    suffix, by anything spellable as a string -- trips it. The legitimate registry lookup compares
    only against ``None``, which is an ``ast.Constant`` of NoneType and therefore untouched.
    """
    source = Path(repo_map.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    fns = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "_references_and_calls_for_path"
    ]
    assert len(fns) == 1, f"expected exactly one dispatch definition, found {len(fns)}"

    body = [
        stmt
        for stmt in fns[0].body
        if not (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant))
    ]
    assert body, "the dispatch body is empty apart from its docstring -- this guard saw nothing"

    offenders: list[str] = []
    for stmt in body:
        for node in ast.walk(stmt):
            if not isinstance(node, ast.Compare):
                continue
            operands = [node.left, *node.comparators]
            for operand in operands:
                if isinstance(operand, ast.Constant) and isinstance(operand.value, str):
                    offenders.append(ast.unparse(node))

    assert not offenders, (
        "the dispatch compares against string constant(s), i.e. a language/suffix ladder has "
        f"returned instead of a pure registry lookup: {offenders}"
    )
