from __future__ import annotations

import os
import time

from tensor_grep.cli import repo_map


def test_reverse_importers_preserves_dotted_and_path_style_import_edges() -> None:
    files = [
        "C:/repo/src/payments.py",
        "C:/repo/src/workflow.py",
        "C:/repo/src/ui.py",
    ]
    imports_by_file = {
        "C:/repo/src/workflow.py": ["src.payments.create_invoice"],
        "C:/repo/src/ui.py": ["../src/workflow"],
    }

    reverse = repo_map._reverse_importers(files, imports_by_file)

    assert reverse["C:/repo/src/payments.py"] == {"C:/repo/src/workflow.py"}
    assert reverse["C:/repo/src/workflow.py"] == {"C:/repo/src/ui.py"}


def test_reverse_importers_stays_bounded_for_large_cached_session_maps() -> None:
    file_count = 1500
    files = [f"C:/repo/src/mod_{index}.py" for index in range(file_count)]
    imports_by_file = {
        file_path: [
            f"mod_{(index + 1) % file_count}",
            f"pkg.mod_{(index + 2) % file_count}",
        ]
        for index, file_path in enumerate(files)
    }

    started_at = time.perf_counter()
    reverse = repo_map._reverse_importers(files, imports_by_file)
    elapsed = time.perf_counter() - started_at

    assert reverse["C:/repo/src/mod_1.py"]
    # Coarse catastrophic-regression sanity bound (an O(n^2)/O(n^3) blow-up on 1500 files is
    # seconds-to-minutes), NOT a tight perf gate -- the real latency gate is the benchmark suite.
    # The tight 1.0s wall-clock flaked on loaded Windows CI runners (observed 1.14s of pure runner
    # jitter, no code change); 10.0s keeps a ~9x jitter margin while still catching a real blow-up.
    assert elapsed < 10.0


def test_repo_context_root_caches_obey_entry_cap(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TENSOR_GREP_REPO_CONTEXT_CACHE_MAX_ROOTS", "2")
    repo_map._JS_TS_REPO_CONTEXTS.clear()
    repo_map._RUST_REPO_CONTEXTS.clear()
    roots = []
    for index in range(3):
        root = tmp_path / f"repo_{index}"
        root.mkdir()
        roots.append(root.resolve())
        repo_map._js_ts_repo_context(root)
        repo_map._rust_repo_context(root)

    assert len(repo_map._JS_TS_REPO_CONTEXTS) == 2
    assert str(roots[0]) not in repo_map._JS_TS_REPO_CONTEXTS
    assert str(roots[1]) in repo_map._JS_TS_REPO_CONTEXTS
    assert str(roots[2]) in repo_map._JS_TS_REPO_CONTEXTS
    assert len(repo_map._RUST_REPO_CONTEXTS) == 2
    assert str(roots[0]) not in repo_map._RUST_REPO_CONTEXTS
    assert str(roots[1]) in repo_map._RUST_REPO_CONTEXTS
    assert str(roots[2]) in repo_map._RUST_REPO_CONTEXTS


def test_reverse_import_pagerank_caps_broad_query_seed_sets() -> None:
    file_count = 3690
    files = [f"C:/repo/src/mod_{index}.py" for index in range(file_count)]
    reverse = {file_path: set() for file_path in files}

    started_at = time.perf_counter()
    ranks = repo_map._personalized_reverse_import_pagerank(
        files[:3173],
        files,
        reverse,
    )
    elapsed = time.perf_counter() - started_at

    assert ranks
    # Regression guard, not a micro-benchmark: with _GRAPH_PAGERANK_SEED_FILE_LIMIT=64 the capped
    # run is ~0.5s; an UNCAPPED regression (all 3173 seeds) is ~50x the work (~25s+). A tight 2.0s
    # bound false-failed on loaded/hardlink-degraded CI runners (observed 2.7s on windows-py3.11),
    # so use a generous ceiling that still catches the O(seeds) blowup this test exists to prevent.
    assert elapsed < 10.0


# ---------------------------------------------------------------------------------------------
# dogfood finding 1 (agent/codemap --deadline post-map bounding): _personalized_reverse_import_
# pagerank ran its 12-iteration loop fully UNBOUNDED even when a caller (context-pack scoring)
# already had a deadline_monotonic in scope -- the loop was never threaded a deadline at all. Two
# council must-fixes: (1) ABANDON to {} at the iteration boundary on expiry (deterministic --
# callers already do `.get(x, 0.0)`, never a partial-ranks lie); (2) hoist the per-node
# `sorted(reverse_importers.get(current))` OUT of the loop, since reverse_importers never changes
# across iterations -- a free, additive speedup independent of the deadline fix.
# ---------------------------------------------------------------------------------------------


def test_pagerank_abandons_to_empty_dict_on_already_expired_deadline() -> None:
    files = [f"C:/repo/src/mod_{index}.py" for index in range(20)]
    # A real hub (non-empty reverse-importers) so a pre-fix run would still do real sort work on
    # iteration 0 before this test's expired deadline should stop it from ever getting there.
    reverse = {files[0]: set(files[1:])}
    flag = repo_map._DeadlineBreakFlag()

    ranks = repo_map._personalized_reverse_import_pagerank(
        files[:2],
        files,
        reverse,
        deadline_monotonic=time.monotonic() - 1.0,
        deadline_hit=flag,
    )

    assert ranks == {}
    assert flag.hit is True


def test_pagerank_deadline_hit_flag_is_optional() -> None:
    """A caller that passes deadline_monotonic but no deadline_hit flag must not crash -- the
    None-guard on `deadline_hit.hit = True` mirrors every sibling _DeadlineBreakFlag call site."""
    files = [f"C:/repo/src/mod_{index}.py" for index in range(5)]
    reverse = {files[0]: {files[1]}}

    ranks = repo_map._personalized_reverse_import_pagerank(
        files[:1],
        files,
        reverse,
        deadline_monotonic=time.monotonic() - 1.0,
    )

    assert ranks == {}


def test_pagerank_deadline_none_is_unaffected() -> None:
    """No deadline supplied (the pre-existing call sites' shape) -> unchanged, non-empty ranks."""
    files = [f"C:/repo/src/mod_{index}.py" for index in range(10)]
    reverse = {files[0]: {files[1], files[2]}}

    ranks = repo_map._personalized_reverse_import_pagerank(files[:1], files, reverse)

    assert ranks
    assert files[0] in ranks


def test_pagerank_hoisted_sort_matches_reference_computation() -> None:
    """The hoist (sorted(reverse_importers.get(current)) computed ONCE before the 12-iteration
    loop, not recomputed every iteration) must be numerically a pure refactor. Prove it against an
    independent reference implementation that keeps the pre-fix recompute-every-iteration shape."""
    files = [f"C:/repo/src/mod_{index}.py" for index in range(8)]
    reverse = {
        files[0]: {files[1], files[2], files[3]},
        files[1]: {files[4]},
        files[4]: {files[5], files[6]},
    }

    def _reference(
        seed_files: list[str],
        all_files: list[str],
        reverse_importers: dict[str, set[str]],
        *,
        alpha: float = 0.85,
        iterations: int = 12,
    ) -> dict[str, float]:
        all_file_set = set(all_files)
        seen: set[str] = set()
        unique_seeds: list[str] = []
        for current in seed_files:
            if current not in all_file_set or current in seen:
                continue
            seen.add(current)
            unique_seeds.append(current)
        seed_set = set(unique_seeds)
        seed_weight = 1.0 / len(unique_seeds)
        personalization = {
            current: (seed_weight if current in seed_set else 0.0) for current in all_files
        }
        ranks = dict(personalization)
        for _ in range(iterations):
            updated = {current: (1.0 - alpha) * personalization[current] for current in all_files}
            for current in all_files:
                # pre-fix shape: recompute the sort every iteration.
                outgoing = sorted(reverse_importers.get(current, set()))
                if outgoing:
                    share = alpha * ranks[current] / len(outgoing)
                    for importer in outgoing:
                        updated[importer] = updated.get(importer, 0.0) + share
                    continue
                spill = alpha * ranks[current] / len(unique_seeds)
                for seed in unique_seeds:
                    updated[seed] = updated.get(seed, 0.0) + spill
            ranks = updated
        return {current: rank for current, rank in ranks.items() if rank > 0.0}

    expected = _reference(files[:2], files, reverse)
    actual = repo_map._personalized_reverse_import_pagerank(files[:2], files, reverse)

    assert actual == expected
    assert actual  # sanity: the fixture actually produces non-trivial ranks


def test_context_tests_skip_framework_scan_without_cheap_test_evidence(monkeypatch) -> None:
    def _fail_unrelated_framework_scan(test_path: str) -> tuple[str, ...]:
        raise AssertionError(f"unexpected framework scan for {test_path}")

    monkeypatch.setattr(
        repo_map,
        "_javascript_test_function_candidates",
        _fail_unrelated_framework_scan,
    )

    tests = [
        f"C:/repo/tests/unrelated_{index}.spec.ts"
        for index in range(repo_map._FRAMEWORK_TEST_PATTERN_SMALL_TEST_LIMIT + 1)
    ]

    matches = repo_map._context_tests(
        ["C:/repo/src/payments.py"],
        tests,
        ["invoice"],
        imports_by_file={},
        file_distances={},
        graph_scores={},
        file_scores={"C:/repo/src/payments.py": 10},
        raw_query="invoice total",
    )

    assert matches == []


def test_edit_plan_context_limits_test_matching_to_requested_file_budget(monkeypatch) -> None:
    repo_payload = {
        "version": 1,
        "path": "C:/repo",
        "files": [f"C:/repo/src/agent_skill_{index}.py" for index in range(10)],
        "symbols": [
            {
                "name": f"loadAgentSkillMatrixAndSkillIndex{index}",
                "kind": "function",
                "file": f"C:/repo/src/agent_skill_{index}.py",
                "line": 1,
                "provenance": "parser-backed",
            }
            for index in range(10)
        ],
        "imports": [],
        "tests": [f"C:/repo/tests/test_agent_skill_{index}.py" for index in range(10)],
    }
    seen_source_counts: list[int] = []

    def _record_context_tests(
        source_files,
        tests,
        terms,
        imports_by_file,
        file_distances,
        graph_scores,
        file_scores,
        *,
        raw_query=None,
    ):
        seen_source_counts.append(len(source_files))
        return []

    monkeypatch.setattr(repo_map, "_context_tests", _record_context_tests)

    repo_map.build_context_edit_plan_from_map(
        repo_payload,
        "loadAgentSkillMatrixAndSkillIndex",
        max_files=3,
    )

    assert seen_source_counts == [3]


def test_edit_plan_blast_radius_uses_bounded_repo_map_for_large_cached_maps(
    monkeypatch,
) -> None:
    repo_payload = {
        "version": 1,
        "path": "C:/repo",
        "files": [f"C:/repo/src/agent_skill_{index}.py" for index in range(400)],
        "symbols": [
            {
                "name": "loadAgentSkillMatrixAndSkillIndex" if index == 0 else f"helper_{index}",
                "kind": "function",
                "file": f"C:/repo/src/agent_skill_{index}.py",
                "line": 1,
                "provenance": "parser-backed",
            }
            for index in range(400)
        ],
        "imports": [
            {
                "file": f"C:/repo/src/agent_skill_{index}.py",
                "imports": [f"agent_skill_{(index + 1) % 400}"],
                "provenance": "python-ast",
            }
            for index in range(400)
        ],
        "tests": [f"C:/repo/tests/test_agent_skill_{index}.py" for index in range(400)],
    }
    seen_file_counts: list[int] = []

    def _record_blast_radius(scoped_map, symbol, **_kwargs):
        seen_file_counts.append(len(scoped_map["files"]))
        primary_symbol = next(
            current
            for current in scoped_map["symbols"]
            if current["name"] == "loadAgentSkillMatrixAndSkillIndex"
        )
        return {
            "routing_reason": "symbol-blast-radius",
            "symbol": symbol,
            "definitions": [primary_symbol],
            "callers": [],
            "files": [primary_symbol["file"]],
            "file_matches": [
                {
                    "path": primary_symbol["file"],
                    "depth": 0,
                    "score": 10,
                    "reasons": ["definition"],
                }
            ],
            "tests": [],
            "test_matches": [],
            "caller_tree": [],
            "imports": [],
            "symbols": [primary_symbol],
            "edit_plan_blast_radius_scope": dict(scoped_map["edit_plan_blast_radius_scope"]),
        }

    monkeypatch.setattr(repo_map, "build_symbol_blast_radius_from_map", _record_blast_radius)

    payload = repo_map.build_context_edit_plan_from_map(
        repo_payload,
        "loadAgentSkillMatrixAndSkillIndex",
        max_files=3,
    )

    assert seen_file_counts
    assert max(seen_file_counts) <= 12
    assert payload["edit_plan_seed"]["blast_radius_scope"]["scoped_file_count"] <= 12


# ---------------------------------------------------------------------------------------------
# audit #81 #11: _definition_module_parts / _normalized_module_parts lowercased every path
# segment unconditionally, so on a case-SENSITIVE filesystem (Linux CI/prod) `Foo.py` matched
# `foo.py` -> wrong-file attribution in reverse-import edges. The fold must be gated on
# os.name == "nt" (Windows only), mirroring _definition_file_dedupe_key's existing platform gate.
# ---------------------------------------------------------------------------------------------


class _OSNameOverride:
    """Delegates every attribute to the real `os` module except `.name`, which is overridden.

    Lets a test simulate `os.name` on the OPPOSITE platform for repo_map's own case-fold gate
    without also flipping `pathlib.Path`'s internal flavor selection, which reads the real,
    global `os` module directly (a separate binding pathlib owns itself). Monkeypatching
    `os.name` directly (e.g. `monkeypatch.setattr(repo_map.os, "name", "posix")`) mutates that
    SAME shared `os` module object process-wide, so `Path(...)` then tries to instantiate
    `PosixPath` on a real Windows box and raises `NotImplementedError: cannot instantiate
    'PosixPath' on your system` before repo_map's own logic is ever reached. Rebinding just the
    module-level name `os` inside `repo_map`'s own namespace (`monkeypatch.setattr(repo_map,
    "os", _OSNameOverride(...))`) avoids that: `Path` construction still goes through the real,
    untouched `os` module, while `repo_map`'s own `os.name` reads see the simulated value.
    """

    def __init__(self, name: str) -> None:
        self.name = name

    def __getattr__(self, attr: str):
        return getattr(os, attr)


def test_definition_module_parts_case_folds_only_on_windows(monkeypatch) -> None:
    monkeypatch.setattr(repo_map, "os", _OSNameOverride("posix"))
    assert repo_map._definition_module_parts("pkg/Foo.py") == ["pkg", "Foo"]
    assert repo_map._definition_module_parts("pkg/foo.py") == ["pkg", "foo"]

    monkeypatch.setattr(repo_map, "os", _OSNameOverride("nt"))
    assert repo_map._definition_module_parts("pkg/Foo.py") == ["pkg", "foo"]
    assert repo_map._definition_module_parts("pkg/foo.py") == ["pkg", "foo"]


def test_definition_module_parts_init_stripping_stays_case_insensitive(monkeypatch) -> None:
    """The __init__/index/mod magic-name strip is unaffected by the platform gate on either
    platform -- these are real on-disk filenames that are always already lowercase by language
    convention (CPython only ever treats a literal __init__.py as a package initializer)."""
    monkeypatch.setattr(repo_map, "os", _OSNameOverride("posix"))
    assert repo_map._definition_module_parts("pkg/__init__.py") == ["pkg"]

    monkeypatch.setattr(repo_map, "os", _OSNameOverride("nt"))
    assert repo_map._definition_module_parts("pkg/__init__.py") == ["pkg"]


def test_normalized_module_parts_case_folds_only_on_windows(monkeypatch) -> None:
    monkeypatch.setattr(repo_map, "os", _OSNameOverride("posix"))
    assert repo_map._normalized_module_parts("pkg.Foo") == ["pkg", "Foo"]

    monkeypatch.setattr(repo_map, "os", _OSNameOverride("nt"))
    assert repo_map._normalized_module_parts("pkg.Foo") == ["pkg", "foo"]


def test_module_path_matches_definition_no_cross_case_false_edge_on_posix(monkeypatch) -> None:
    """The end-to-end regression this finding is about: on a case-SENSITIVE filesystem, a
    module named `foo` must NOT match a definition file `Foo.py` (that cross-case pairing would
    be a wrong-file attribution in a reverse-import edge) -- only an exact-case match may
    succeed."""
    monkeypatch.setattr(repo_map, "os", _OSNameOverride("posix"))

    assert repo_map._module_path_matches_definition("foo", "pkg/Foo.py") is False
    assert repo_map._module_path_matches_definition("Foo", "pkg/Foo.py") is True
    assert repo_map._module_path_matches_definition("foo", "pkg/foo.py") is True


def test_module_path_matches_definition_case_insensitive_on_windows(monkeypatch) -> None:
    """Preserves the pre-existing Windows behavior (case-insensitive match) unchanged."""
    monkeypatch.setattr(repo_map, "os", _OSNameOverride("nt"))

    assert repo_map._module_path_matches_definition("foo", "pkg/Foo.py") is True
