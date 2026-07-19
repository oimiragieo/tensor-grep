"""Real-workspace-scale residual of #220/#669 (#222): `_detect_vendored_subtrees`'s outermost-
nested-chain DEDUP loop, not just its manifest-probe loop, was the dominant super-linear cost --
an OLD-vs-NEW real-binary re-verify on a real ~50k-file/40-sibling-project workspace found that
the #220/#669 fix's per-iteration checks bound the ITERATION COUNT correctly, but each individual
iteration's own cost was `_path_is_relative_to(root / rel_dir, root / existing)` (two real
`Path.resolve()` filesystem syscalls -- Windows `nt._getfinalpathname`, independently documented
expensive by `_precomputed_validation_files_for_root`'s own docstring) INSIDE an O(candidate_
roots^2)-shaped loop, so a single outer iteration's cost grows with `len(subtree_rel_roots)`.

On a synthetic sized to keep candidate roots INDEPENDENT (no common absorbing ancestor -- unlike
`tests/integration/test_agent_cold_deadline_tail_sla_220.py`'s `manifest_heavy_repo` fixture,
where each project's OWN root manifest absorbs its nested `packages/pkgN` children early in the
depth-sorted dedup pass and keeps the effective candidate count small), the unbounded per-call
cost of `_detect_vendored_subtrees` was measured to scale super-linearly (~quadratic) with
candidate-root count: 7.7s at 120 candidates, 20.7s at 200, 40.9s at 304 (cProfile on the smallest
of these attributed 88-92% of wall-clock to this one dedup genexpr, ~61% to
`nt._getfinalpathname` alone). This is DISTINCT from #220's own fixture, which never grows
`subtree_rel_roots` large enough (each project absorbs only 2 nested packages) to expose this.

Fix: `rel_dir`/`existing` are both already lexically relative to the same resolved `root` (built
via a plain `.relative_to(root)`, no resolve() of its own), so nesting is a pure `.parts` PREFIX
test -- no filesystem I/O. Same shape as this function's own STRONG-3 fix and PR #670's `_tier`
helper (`repo_map.py`). Measured 90.8x faster on the exact 304-candidate fixture that motivated
this fix (40.86s -> 0.45s), and the previously CPU-unsafe-to-measure 2400-candidate scale (would
have extrapolated to tens of minutes) now completes in ~5.9s.

A second, independent gap this fix closes: agent_capsule's `skipped_assembly_stages` only ever
named call-1's OWN `_detect_vendored_subtrees` invocation ("vendored_subtree_detection") -- a
SECOND call inside `repo_map._build_context_pack_from_map`'s `auto_deweight` pass (repo_map.py's
`build_context_pack_from_map`, called via `build_context_render_from_map`) could trip its OWN
internal deadline_hit and correctly set `payload["partial"] = True`, but nothing named WHICH
assembly stage inside that call actually consumed the budget -- reproduced empirically: a capsule
could show `partial: true, deadline_limit.deadline_exceeded: true` with an EMPTY/absent
`assembly_stages_skipped`. `build_context_render_from_map` gained a new, additive, default-None
`deadline_hit` passthrough so a caller (agent_capsule) can observe this and name it honestly
(`"context_pack_assembly"` -- deliberately NOT "vendored_subtree_detection" again, since the
shared flag also covers the symbol-scoring and pagerank sibling loops inside that same call, and
mislabeling would overclaim precision this signal alone cannot support).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tensor_grep.cli import agent_capsule as _agent_capsule
from tensor_grep.cli import orient_capsule as _orient_capsule
from tensor_grep.cli import repo_map as _repo_map

# Independent (non-absorbed) candidate count: large enough that the OLD resolve()-based dedup
# loop was measured to take double-digit seconds (20.7s at this exact count), small enough that
# fixture construction + the (now-fast, post-fix) test itself stay well under any CI budget.
_INDEPENDENT_PACKAGE_COUNT = 200


def _rm_with_many_independent_manifest_roots(root: Path) -> dict[str, Any]:
    """`_INDEPENDENT_PACKAGE_COUNT` sibling manifest-bearing directories under a wrapper dir that
    is ITSELF neither a STRONG-0 vendor name nor manifest-bearing (`deps/`) -- so none of them
    nest under a common already-accepted ancestor the way #220's `manifest_heavy_repo` fixture's
    `packages/pkgN` children nest under their own project root. This keeps `subtree_rel_roots`
    growing roughly 1:1 with processed candidates, which is what the OLD O(candidate_roots^2)
    resolve()-per-pair dedup scaled badly with, and what the fix's `.parts`-prefix comparison
    does not."""
    files: list[str] = []
    imports: list[dict[str, Any]] = []
    for i in range(_INDEPENDENT_PACKAGE_COUNT):
        pkg = root / "deps" / f"pkg_{i:05d}"
        pkg.mkdir(parents=True)
        (pkg / "package.json").write_text('{"name": "pkg"}', encoding="utf-8")
        index_js = pkg / "index.js"
        index_js.write_text("module.exports = function stub() { return 1; };\n", encoding="utf-8")
        files.append(str(index_js))
    (root / "app.py").write_text("def main():\n    return 1\n", encoding="utf-8")
    files.append(str(root / "app.py"))
    return {"path": str(root), "files": files, "imports": imports, "symbols": []}


# ---------------------------------------------------------------------------------------------
# Property 1: the dedup loop no longer calls the resolve()-based `_path_is_relative_to` at all.
# ---------------------------------------------------------------------------------------------


def test_dedup_loop_never_calls_resolve_based_path_is_relative_to(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Direct regression guard for the #222 fix: monkeypatch `_repo_map._path_is_relative_to` to
    raise if called at all, then run `_detect_vendored_subtrees` over a shape that exercises the
    outermost-chain dedup loop (multiple independent manifest roots). If a future edit reverts to
    the resolve()-based nesting check, this test fails immediately and points at the exact
    function -- a much cheaper regression signal than re-deriving the wall-clock numbers."""

    def _boom(path: Path, parent: Path) -> bool:
        raise AssertionError(
            "orient_capsule._detect_vendored_subtrees' outermost-chain dedup loop called the "
            "resolve()-based _path_is_relative_to again -- this reintroduces the #222 "
            "O(candidate_roots^2)-real-filesystem-syscall super-linear cost. Use a lexical "
            ".parts-prefix comparison instead (see the #222 fix comment in orient_capsule.py)."
        )

    monkeypatch.setattr(_repo_map, "_path_is_relative_to", _boom)
    rm = _rm_with_many_independent_manifest_roots(tmp_path)
    # Must not raise -- proves the dedup loop's nesting check never reaches the patched function.
    result = _orient_capsule._detect_vendored_subtrees(rm)
    assert isinstance(result, dict)


# ---------------------------------------------------------------------------------------------
# Property 2: byte-identical output on a REAL nesting scenario (STRONG-0 root absorbs a nested
# STRONG-1 manifest child) -- the fix must not change WHAT gets de-weighted, only how fast.
# ---------------------------------------------------------------------------------------------


def test_dedup_still_subsumes_nested_manifest_under_strong0_root(tmp_path: Path) -> None:
    """Mirrors `test_orient_deweight_vendored.py`'s nesting-dedup coverage but specifically
    targets the OUTERMOST-CHAIN DEDUP LOOP this fix touched: a `third_party/` STRONG-0 vendor
    root with a NESTED STRONG-1-manifest child must still collapse to ONE result entry (the
    outer root), never two separately-reported, overlapping subtrees."""
    (tmp_path / "third_party" / "pkgA").mkdir(parents=True)
    (tmp_path / "third_party" / "pkgA" / "package.json").write_text('{"name":"a"}')
    (tmp_path / "third_party" / "pkgA" / "index.js").write_text("module.exports = 1;\n")
    rm = {
        "path": str(tmp_path),
        "files": [
            str(tmp_path / "third_party" / "pkgA" / "package.json"),
            str(tmp_path / "third_party" / "pkgA" / "index.js"),
        ],
        "imports": [],
        "symbols": [],
    }
    result = _orient_capsule._detect_vendored_subtrees(rm)
    keys_rel = sorted(str(Path(k).relative_to(tmp_path)).replace("\\", "/") for k in result)
    assert keys_rel == ["third_party"], (
        f"expected the nested pkgA manifest to be subsumed under the outer third_party STRONG-0 "
        f"root as a SINGLE entry, got {keys_rel}"
    )


# ---------------------------------------------------------------------------------------------
# Property 3: no-deadline-pressure byte-identity for the dedup-loop change itself.
# ---------------------------------------------------------------------------------------------


def test_dedup_result_identical_with_and_without_far_future_deadline(tmp_path: Path) -> None:
    """The #205/#220 discipline: a deadline comfortably in the future must produce IDENTICAL
    output to no deadline at all -- the dedup-loop rewrite is a pure performance change, never a
    behavior change absent deadline pressure."""
    rm = _rm_with_many_independent_manifest_roots(tmp_path)
    no_deadline = _orient_capsule._detect_vendored_subtrees(rm)
    far_future = _orient_capsule._detect_vendored_subtrees(
        rm, deadline_monotonic=__import__("time").monotonic() + 3600.0
    )
    assert no_deadline == far_future


# ---------------------------------------------------------------------------------------------
# Property 4: the call-2 enumeration-gap fix -- build_context_render_from_map's new deadline_hit
# passthrough actually observes a trip from INSIDE build_context_pack_from_map.
# ---------------------------------------------------------------------------------------------


def test_build_context_render_from_map_deadline_hit_passthrough_observes_internal_trip(
    tmp_path: Path,
) -> None:
    """Regression guard for the #222 enumeration-gap fix: before this fix,
    `build_context_render_from_map` had no way to report "something inside my own
    `build_context_pack_from_map` call broke on --deadline" to a caller -- a caller-owned
    `_DeadlineBreakFlag` passed in as `deadline_hit` must come back `.hit == True` when the
    already-exceeded deadline forces `_build_context_pack_from_map`'s symbol-scoring loop to
    break on its very first iteration."""
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("def bar():\n    return 2\n", encoding="utf-8")
    rm = _repo_map.build_repo_map(str(tmp_path))
    assert rm.get("symbols"), "fixture must have at least one symbol to exercise the scoring loop"

    flag = _repo_map._DeadlineBreakFlag()
    already_past = __import__("time").monotonic() - 1.0
    payload = _repo_map.build_context_render_from_map(
        rm, "foo", deadline_monotonic=already_past, deadline_hit=flag
    )
    assert flag.hit is True, (
        "an already-exceeded deadline passed into build_context_render_from_map must set the "
        "caller-supplied deadline_hit flag -- the #222 passthrough is not wired correctly"
    )
    assert payload.get("partial") is True


def test_build_context_render_from_map_deadline_hit_none_default_is_backward_compatible(
    tmp_path: Path,
) -> None:
    """Every pre-#222 caller of `build_context_render_from_map` passes no `deadline_hit` at all
    -- the new parameter must default to None and never require callers to opt in."""
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    rm = _repo_map.build_repo_map(str(tmp_path))
    payload = _repo_map.build_context_render_from_map(rm, "foo")
    assert isinstance(payload, dict)


# ---------------------------------------------------------------------------------------------
# Property 5: agent_capsule now honestly names an internal context-pack-assembly deadline trip
# instead of silently dropping it from assembly_stages_skipped.
# ---------------------------------------------------------------------------------------------


def test_agent_capsule_names_context_pack_assembly_when_internal_stage_trips(
    tmp_path: Path,
) -> None:
    """End-to-end proof of the #222 enumeration-gap fix through the real cold-path entry point:
    force the deadline to already be exceeded by the time `build_context_render_from_map` runs
    (a query with no vendored-subtree signal at all, so call-1's own
    `vendored_subtree_detection` flag never fires), and confirm `context_pack_assembly` appears
    in `assembly_stages_skipped` -- proving the SECOND, previously-invisible truncation site is
    now enumerated."""
    (tmp_path / "a.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("def bar():\n    return 2\n", encoding="utf-8")

    already_past = __import__("time").monotonic() - 1.0
    result = _agent_capsule.build_agent_capsule(
        "foo",
        str(tmp_path),
        deadline_monotonic=already_past,
    )
    assert result.get("partial") is True, result
    skipped = result.get("deadline_limit", {}).get("assembly_stages_skipped") or []
    assert "context_pack_assembly" in skipped, (
        f"an already-exceeded deadline must name the context-pack assembly stage as skipped, "
        f"got assembly_stages_skipped={skipped!r} (deadline_limit={result.get('deadline_limit')!r})"
    )
