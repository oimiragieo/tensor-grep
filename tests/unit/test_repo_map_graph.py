from __future__ import annotations

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
